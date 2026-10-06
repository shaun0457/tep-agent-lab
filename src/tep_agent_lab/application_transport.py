"""Versioned JSON envelopes over an already-constructed application service."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import json
from typing import Any, Literal

from industrial_agent_runtime import to_jsonable
from industrial_agent_runtime.serialization import freeze_json

from .application_views import (
    AmbiguousSignal, ApplicationViewService, UnknownEntity, UnknownSignal,
)
from .playground_views import ViewUnavailable, VisibilityViolation

PROTOCOL_VERSION = "tep-agent-lab.application-transport/v0"
MAX_REQUEST_BYTES = 64 * 1024


class ErrorCode(StrEnum):
    UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
    UNKNOWN_SIGNAL = "UNKNOWN_SIGNAL"
    AMBIGUOUS_SIGNAL = "AMBIGUOUS_SIGNAL"
    VIEW_UNAVAILABLE = "VIEW_UNAVAILABLE"
    VISIBILITY_VIOLATION = "VISIBILITY_VIOLATION"
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
    ErrorCode.INVALID_REQUEST: "invalid request",
    ErrorCode.UNSUPPORTED_METHOD: "unsupported method",
    ErrorCode.UNSUPPORTED_PROTOCOL_VERSION: "unsupported protocol version",
    ErrorCode.INTERNAL_ERROR: "internal error",
}
_REQUIRED = {
    "get_run": {"run_id"},
    "get_entity": {"run_id", "entity_id"},
    "get_signal": {"run_id", "signal_id"},
    "get_signal_history": {"run_id", "signal_id"},
}
_HISTORY_OPTIONS = {"start_hours", "end_hours", "max_points"}


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


def _validate(raw: Mapping[str, Any]) -> ErrorCode | None:
    if (set(raw) != {"protocol_version", "request_id", "method", "params"}
            or any(type(raw[key]) is not str
                   for key in ("protocol_version", "request_id", "method"))
            or not isinstance(raw["params"], Mapping)):
        return ErrorCode.INVALID_REQUEST
    if raw["protocol_version"] != PROTOCOL_VERSION:
        return ErrorCode.UNSUPPORTED_PROTOCOL_VERSION
    method, params = raw["method"], raw["params"]
    if method not in _REQUIRED:
        return ErrorCode.UNSUPPORTED_METHOD
    required = _REQUIRED[method]
    optional = _HISTORY_OPTIONS if method == "get_signal_history" else set()
    if (not required <= set(params) or set(params) - required - optional
            or any(type(params[key]) is not str for key in required)):
        return ErrorCode.INVALID_REQUEST
    for key in ("start_hours", "end_hours"):
        if key in params and params[key] is not None and type(params[key]) not in (int, float):
            return ErrorCode.INVALID_REQUEST
    if "max_points" in params and type(params["max_points"]) is not int:
        return ErrorCode.INVALID_REQUEST
    # JSON safety is schema validation; ranges/order/bounds belong to the service.
    try:
        to_jsonable(params)
    except (TypeError, ValueError, OverflowError):
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
        request_id = request.get("request_id")
        if type(request_id) is not str:
            request_id = None
        code = _validate(request)
        if code is not None:
            return _failure(request_id, code)
        method, params = request["method"], request["params"]
        try:
            # Explicit calls only; client strings never resolve Python attributes.
            if method == "get_run":
                result = self._service.get_run(**params)
            elif method == "get_entity":
                result = self._service.get_entity(**params)
            elif method == "get_signal":
                result = self._service.get_signal(**params)
            else:  # Validated get_signal_history.
                result = self._service.get_signal_history(**params)
        except UnknownEntity:
            return _failure(request_id, ErrorCode.UNKNOWN_ENTITY)
        except UnknownSignal:
            return _failure(request_id, ErrorCode.UNKNOWN_SIGNAL)
        except AmbiguousSignal:
            return _failure(request_id, ErrorCode.AMBIGUOUS_SIGNAL)
        except ViewUnavailable:
            return _failure(request_id, ErrorCode.VIEW_UNAVAILABLE)
        except VisibilityViolation:
            return _failure(request_id, ErrorCode.VISIBILITY_VIOLATION)
        except ValueError:
            # History's application validation owns numeric ranges and window order.
            code = ErrorCode.INVALID_REQUEST if method == "get_signal_history" else ErrorCode.INTERNAL_ERROR
            return _failure(request_id, code)
        except Exception:
            return _failure(request_id, ErrorCode.INTERNAL_ERROR)
        try:
            return ApplicationSuccess(request_id, to_jsonable(result))
        except Exception:
            return _failure(request_id, ErrorCode.INTERNAL_ERROR)
