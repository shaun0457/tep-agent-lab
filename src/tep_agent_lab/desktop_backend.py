"""Persistent developer-demo host; stdout carries only application NDJSON."""

import argparse
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path
import sys
from typing import BinaryIO

from industrial_agent_runtime.serialization import canonical_json

from .application_transport import ApplicationTransport, MAX_REQUEST_BYTES
from .application_views import ApplicationViewService
from .e0_demo import DEFAULT_RUN_ID, run_demo
from .playground import RunStatus


def serve(transport: ApplicationTransport, source: BinaryIO, sink: BinaryIO) -> None:
    """Bound each frame before parsing, drain oversize frames, and flush each reply.

    LF or CRLF is framing, not part of the JSON byte limit. A final frame without
    a newline is processed before EOF. Oversize prefixes still go through the
    transport codec, which owns the failure envelope and schema validation.
    """
    while True:
        frame = source.readline(MAX_REQUEST_BYTES + 2)
        if not frame:
            return
        if frame.endswith(b"\n"):
            payload = frame[:-1]
            if payload.endswith(b"\r"):
                payload = payload[:-1]
        else:
            payload = frame
            if len(frame) > MAX_REQUEST_BYTES:
                while True:
                    chunk = source.readline(MAX_REQUEST_BYTES + 2)
                    if not chunk or chunk.endswith(b"\n"):
                        break
        response = transport.dispatch_json(payload)
        sink.write((canonical_json(response) + "\n").encode("utf-8"))
        sink.flush()


def main(argv: Sequence[str] | None = None) -> int:
    # Even argparse help belongs on stderr: stdout is exclusively the wire.
    with redirect_stdout(sys.stderr):
        parser = argparse.ArgumentParser(description="E0 developer-demo desktop backend")
        parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
        parser.add_argument("--output-root", type=Path, default=Path("runs/e0-desktop"))
        args = parser.parse_args(argv)
    wire = sys.stdout.buffer
    try:
        # Trusted Python diagnostics from bootstrap/dispatch must never enter wire.
        with redirect_stdout(sys.stderr):
            demo = run_demo(args.output_root, args.run_id)
            if demo.outcome.terminal_status != RunStatus.COMPLETED:
                raise RuntimeError("developer demo did not complete")
            transport = ApplicationTransport(ApplicationViewService(demo.manager.queries))
            # demo retains its manager/completed in-memory session for the whole loop.
            # RunManager.start already closes simulator handles in its finally block.
            serve(transport, sys.stdin.buffer, wire)
    except Exception as exc:
        print(f"desktop backend failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
