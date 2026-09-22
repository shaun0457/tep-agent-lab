"""Single-run, single-writer append-only storage; not a cross-run memory service.

Artifact checksums are storage identities, not a replacement InformationRef envelope.
Callers publish references using industrial-agent-runtime's InformationRef contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


class CorruptRunLog(ValueError):
    """Stored data failed integrity validation; never silently repair history."""


def canonical_json(value: Any) -> bytes:
    """Encode JSON data without NaN, duplicate aliases or platform whitespace."""
    def validate(item: Any) -> None:
        if item is None or isinstance(item, (str, bool, int, float)):
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            for child in item.values():
                validate(child)
            return
        raise TypeError("Expected JSON data with string mapping keys")

    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def content_checksum(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class RunLog:
    """One host-owned directory per run, serialized by the coordinator.

    Concurrent writers/processes are deliberately unsupported. A partial final
    write is detected on reopening and requires explicit operator recovery.
    """

    def __init__(self, directory: Path | str, manifest: dict[str, Any] | None = None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.directory / "manifest.json"
        self._events_path = self.directory / "events.jsonl"
        self._artifacts = self.directory / "artifacts"
        self._artifacts.mkdir(exist_ok=True)
        if self._manifest_path.exists():
            existing = self.manifest()
            if manifest is not None and canonical_json(existing) != canonical_json(manifest):
                raise ValueError("Run manifest is immutable")
        elif manifest is None:
            raise ValueError("A new run requires a manifest")
        else:
            if not isinstance(manifest, dict) or not manifest.get("run_id"):
                raise ValueError("Manifest requires a run_id")
            _write_new(self._manifest_path, canonical_json(manifest))
        self.events()  # Fail closed on corrupt history before accepting writes.

    def manifest(self) -> dict[str, Any]:
        value = json.loads(self._manifest_path.read_bytes())
        if not isinstance(value, dict) or not value.get("run_id"):
            raise CorruptRunLog("Invalid run manifest")
        return value

    def events(self) -> tuple[dict[str, Any], ...]:
        if not self._events_path.exists():
            return ()
        raw = self._events_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise CorruptRunLog("Incomplete final event")
        events: list[dict[str, Any]] = []
        previous = None
        try:
            for index, line in enumerate(raw.splitlines()):
                event = json.loads(line)
                checksum = event.pop("checksum")
                if (event["sequence"] != index or event["previous_checksum"] != previous
                        or content_checksum(event) != checksum):
                    raise CorruptRunLog("Invalid event sequence/checksum")
                event["checksum"] = checksum
                events.append(event)
                previous = checksum
        except (KeyError, TypeError, json.JSONDecodeError, AttributeError) as exc:
            raise CorruptRunLog("Invalid event encoding") from exc
        return tuple(events)

    def append(self, event_type: str, payload: Any) -> dict[str, Any]:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("An event type is required")
        history = self.events()
        # JSON round-trip detaches caller data before committing it.
        event = {"sequence": len(history), "type": event_type,
                 "payload": json.loads(canonical_json(payload)),
                 "previous_checksum": history[-1]["checksum"] if history else None}
        event["checksum"] = content_checksum(event)
        encoded = canonical_json(event) + b"\n"
        with self._events_path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def put_artifact(self, value: Any) -> str:
        data = canonical_json(value)
        checksum = hashlib.sha256(data).hexdigest()
        path = self._artifacts / f"{checksum}.json"
        if path.exists():
            self.read_artifact(checksum)
        else:
            _write_new(path, data)
        return checksum

    def read_artifact(self, checksum: str) -> Any:
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Invalid artifact checksum")
        data = (self._artifacts / f"{checksum}.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != checksum:
            raise CorruptRunLog("Artifact checksum mismatch")
        return json.loads(data)
