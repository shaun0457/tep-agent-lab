"""Versioned JSON envelopes over an already-constructed application service.

Owning contract: docs/specs/application-api-v1.md §Transport v1. v1 replaces v0; a host
speaks exactly one protocol version.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import json
import re
from typing import Any, Literal

from industrial_agent_runtime import to_jsonable
from industrial_agent_runtime.serialization import freeze_json

from .application_models import InvalidRequest, snapshot_from_json
from .application_views import (
    AmbiguousSignal, ApplicationViewService, UnknownEntity, UnknownSignal,
)
from .playground_views import ViewUnavailable, VisibilityViolation
from .telemetry import (ClockMismatch, ResourceLimitExceeded, SnapshotUnavailable,
                        UnknownBinding)

PROTOCOL_VERSION = "tep-agent-lab.application-transport/v1"
MAX_REQUEST_BYTES = 64 * 1024
MAX_SAFE_INTEGER = 2**53 - 1
# Correlation ids are bounded printable ASCII tokens, so responses always encode.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class ErrorCode(StrEnum):
    UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
    UNKNOWN_SIGNAL = "UNKNOWN_SIGNAL"
    AMBIGUOUS_SIGNAL = "AMBIGUOUS_SIGNAL"
    VIEW_UNAVAILABLE = "VIEW_UNAVAILABLE"
    VISIBILITY_VIOLATION = "VISIBILITY_VIOLATION"
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED_METHOD = "UNSUPPORTED_METHOD"
    UNSUPPORTED_PROTOCOL_VERSION = "UNSUPPORTED_PROTOCOL_VERSION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_MESSAGES = {
    ErrorCode.UNKNOWN_ENTITY: "unknown entity",
    ErrorCode.UNKNOWN_SIGNAL: "unknown signal",
    ErrorCode.AMBIGUOUS_SIGNAL: "ambiguous signal",
    ErrorCode.VIEW_UNAVAILABLE: "view unavailable",
    ErrorCode.VISIBILITY_VIOLATION: "visibility violation",
    ErrorCode.SNAPSHOT_UNAVAILABLE: "snapshot unavailable",
    ErrorCode.RESOURCE_LIMIT_EXCEEDED: "resource limit exceeded",
    ErrorCode.INVALID_REQUEST: "invalid request",
    ErrorCode.UNSUPPORTED_METHOD: "unsupported method",
    ErrorCode.UNSUPPORTED_PROTOCOL_VERSION: "unsupported protocol version",
    ErrorCode.INTERNAL_ERROR: "internal error",
}
_REQUIRED = {
    "open_telemetry_snapshot": {"run_id"},
    "get_run": {"run_id"},
    "get_entity": {"run_id", "entity_id"},
    "get_signal": {"run_id", "signal_id", "snapshot"},
    "get_signal_history": {"run_id", "signal_id", "snapshot"},
}
_OPTIONAL = {
    "open_telemetry_snapshot": {"event_horizon_ticks", "ingest_sequence"},
    "get_run": {"snapshot"},
    "get_entity": set(),
    "get_signal": set(),
    "get_signal_history": {"event_start_ticks", "event_end_ticks", "max_points"},
}
_STRINGS = {"run_id", "entity_id", "signal_id"}
_INTEGERS = {"event_horizon_ticks", "ingest_sequence", "max_points"}
_NULLABLE_INTEGERS = {"event_start_ticks", "event_end_ticks"}


@dataclass(frozen=True)
class ApplicationRequest:
    protocol_version: str
    request_id: str
    method: str
    params: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", freeze_json(self.params))


@dataclass(frozen=True)
class TransportError:
    code: ErrorCode
    message: str


@dataclass(frozen=True)
class ApplicationSuccess:
    request_id: str
    result: Mapping[str, Any]
    protocol_version: str = field(default=PROTOCOL_VERSION, init=False)
    ok: Literal[True] = field(default=True, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "result", freeze_json(self.result))


@dataclass(frozen=True)
class ApplicationFailure:
    request_id: str | None
    error: TransportError
    protocol_version: str = field(default=PROTOCOL_VERSION, init=False)
    ok: Literal[False] = field(default=False, init=False)


ApplicationResponse = ApplicationSuccess | ApplicationFailure


def _failure(request_id: str | None, code: ErrorCode) -> ApplicationFailure:
    return ApplicationFailure(request_id, TransportError(code, _MESSAGES[code]))


def _reject_json_constant(value: str) -> None:
    raise ValueError("invalid JSON constant")


def decode_request_json(payload: bytes | str) -> dict[str, Any] | ApplicationFailure:
    """Bound raw UTF-8 JSON before parsing; schema validation stays in dispatch.

    Codec failures cannot safely recover correlation, so request_id is null.
    """
    try:
        if isinstance(payload, str):
            raw = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            raw = payload
        else:
            return _failure(None, ErrorCode.INVALID_REQUEST)
        if len(raw) > MAX_REQUEST_BYTES:
            return _failure(None, ErrorCode.INVALID_REQUEST)
        decoded = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError, RecursionError):
        return _failure(None, ErrorCode.INVALID_REQUEST)
    if not isinstance(decoded, dict):
        return _failure(None, ErrorCode.INVALID_REQUEST)
    return decoded


def _request_id(value: Any) -> str | None:
    return value if type(value) is str and _REQUEST_ID.fullmatch(value) else None


def _validate(raw: Mapping[str, Any]) -> tuple[ErrorCode | None, dict[str, Any]]:
    """Schema validation; returns the params with ``snapshot`` parsed exactly once."""
    code = _validate_shape(raw)
    if code is not None:
        return code, {}
    params = dict(raw["params"])
    if "snapshot" in params and not (
            raw["method"] == "get_run" and params["snapshot"] is None):
        try:
            params["snapshot"] = snapshot_from_json(params["snapshot"])
        except InvalidRequest:
            return ErrorCode.INVALID_REQUEST, {}
    if "snapshot" in params and params["snapshot"] is not None:
        snapshot = params["snapshot"]
        if (snapshot.ingest_sequence > MAX_SAFE_INTEGER
                or snapshot.event_horizon_ticks > MAX_SAFE_INTEGER):
            return ErrorCode.INVALID_REQUEST, {}
    return None, params


def _validate_shape(raw: Mapping[str, Any]) -> ErrorCode | None:
    if (set(raw) != {"protocol_version", "request_id", "method", "params"}
            or any(type(raw[key]) is not str
                   for key in ("protocol_version", "request_id", "method"))
            or _request_id(raw["request_id"]) is None
            or not isinstance(raw["params"], Mapping)):
        return ErrorCode.INVALID_REQUEST
    if raw["protocol_version"] != PROTOCOL_VERSION:
        return ErrorCode.UNSUPPORTED_PROTOCOL_VERSION
    method, params = raw["method"], raw["params"]
    if method not in _REQUIRED:
        return ErrorCode.UNSUPPORTED_METHOD
    required = _REQUIRED[method]
    if not required <= set(params) or set(params) - required - _OPTIONAL[method]:
        return ErrorCode.INVALID_REQUEST
    for key, value in params.items():
        if key in _STRINGS and type(value) is not str:
            return ErrorCode.INVALID_REQUEST
        if key in _INTEGERS and (type(value) is not int or abs(value) > MAX_SAFE_INTEGER):
            return ErrorCode.INVALID_REQUEST
        if key in _NULLABLE_INTEGERS and value is not None and (
                type(value) is not int or abs(value) > MAX_SAFE_INTEGER):
            return ErrorCode.INVALID_REQUEST
    return None


class ApplicationTransport:
    """No process ownership or attachment: the caller supplies the live service.

    Dispatch accepts a decoded JSON object or frozen request. Responses are frozen
    envelopes; use runtime ``to_jsonable``/``canonical_json`` for wire encoding.
    Malformed requests without a string request_id receive null for correlation.
    Exception text is never included in a client response.
    """

    def __init__(self, service: ApplicationViewService) -> None:
        self._service = service

    def dispatch_json(self, payload: bytes | str) -> ApplicationResponse:
        """Decode bounded raw input, then reuse the decoded-object dispatcher."""
        request = decode_request_json(payload)
        if isinstance(request, ApplicationFailure):
            return request
        return self.dispatch(request)

    def dispatch(self, request: object) -> ApplicationResponse:
        if isinstance(request, ApplicationRequest):
            request = {"protocol_version": request.protocol_version,
                       "request_id": request.request_id, "method": request.method,
                       "params": request.params}
        if not isinstance(request, Mapping):
            return _failure(None, ErrorCode.INVALID_REQUEST)
        request_id = _request_id(request.get("request_id"))
        code, params = _validate(request)
        if code is not None:
            return _failure(request_id, code)
        method = request["method"]
        try:
            # Explicit calls only; client strings never resolve Python attributes.
            if method == "open_telemetry_snapshot":
                result = self._service.open_telemetry_snapshot(**params)
            elif method == "get_run":
                result = self._service.get_run(**params)
            elif method == "get_entity":
                result = self._service.get_entity(**params)
            elif method == "get_signal":
                result = self._service.get_signal(**params)
            else:  # Validated get_signal_history.
                result = self._service.get_signal_history(**params)
        except UnknownEntity:
            return _failure(request_id, ErrorCode.UNKNOWN_ENTITY)
        except (UnknownSignal, UnknownBinding):
            return _failure(request_id, ErrorCode.UNKNOWN_SIGNAL)
        except AmbiguousSignal:
            return _failure(request_id, ErrorCode.AMBIGUOUS_SIGNAL)
        except ViewUnavailable:
            return _failure(request_id, ErrorCode.VIEW_UNAVAILABLE)
        except VisibilityViolation:
            return _failure(request_id, ErrorCode.VISIBILITY_VIOLATION)
        except (SnapshotUnavailable, ClockMismatch):
            return _failure(request_id, ErrorCode.SNAPSHOT_UNAVAILABLE)
        except ResourceLimitExceeded:
            return _failure(request_id, ErrorCode.RESOURCE_LIMIT_EXCEEDED)
        except InvalidRequest:
            # Only application validation and explicitly translated read-boundary
            # validation are client failures. Other TelemetryErrors are server faults.
            return _failure(request_id, ErrorCode.INVALID_REQUEST)
        except Exception:
            return _failure(request_id, ErrorCode.INTERNAL_ERROR)
        try:
            return ApplicationSuccess(request_id, to_jsonable(result))
        except Exception:
            return _failure(request_id, ErrorCode.INTERNAL_ERROR)
