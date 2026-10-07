export const PROTOCOL = "tep-agent-lab.application-transport/v0";
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };
export type Envelope =
  | { protocol_version: typeof PROTOCOL; request_id: string; ok: true; result: JsonObject }
  | { protocol_version: typeof PROTOCOL; request_id: string; ok: false; error: { code: string; message: string } };

const messages: Record<string, string> = {
  UNKNOWN_ENTITY: "unknown entity", UNKNOWN_SIGNAL: "unknown signal",
  AMBIGUOUS_SIGNAL: "ambiguous signal", VIEW_UNAVAILABLE: "view unavailable",
  VISIBILITY_VIOLATION: "visibility violation", INVALID_REQUEST: "invalid request",
  UNSUPPORTED_METHOD: "unsupported method", UNSUPPORTED_PROTOCOL_VERSION: "unsupported protocol version",
  INTERNAL_ERROR: "internal error",
};

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isJson(value: unknown, depth = 0): value is Json {
  if (depth > 128) return false;
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every((item: unknown) => isJson(item, depth + 1));
  return isObject(value) && Object.values(value).every((item) => isJson(item, depth + 1));
}

export class ClientError extends Error {
  readonly code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}

export function validateEnvelope(value: unknown): Envelope {
  const bad = () => new ClientError("BACKEND_PROTOCOL_ERROR", "Invalid application response");
  if (!isObject(value) || Object.keys(value).length !== 4 || value.protocol_version !== PROTOCOL
      || typeof value.request_id !== "string" || !/^desktop-[1-9]\d*$/.test(value.request_id)
      || typeof value.ok !== "boolean") throw bad();
  if (value.ok) {
    if (!("result" in value) || "error" in value || !isObject(value.result) || !isJson(value.result)) throw bad();
    return { protocol_version: PROTOCOL, request_id: value.request_id, ok: true, result: value.result };
  }
  if (!("error" in value) || "result" in value || !isObject(value.error) || Object.keys(value.error).length !== 2
      || typeof value.error.code !== "string" || typeof value.error.message !== "string"
      || !Object.hasOwn(messages, value.error.code) || messages[value.error.code] !== value.error.message) throw bad();
  return { protocol_version: PROTOCOL, request_id: value.request_id, ok: false,
    error: { code: value.error.code, message: value.error.message } };
}

const hostErrors = new Set(["BACKEND_NOT_RUNNING", "BACKEND_IO_ERROR", "BACKEND_PROTOCOL_ERROR",
  "BACKEND_RESPONSE_TOO_LARGE", "BACKEND_EXITED", "BACKEND_TIMEOUT", "INVALID_REQUEST", "UNSUPPORTED_METHOD"]);
export function safeHostError(value: unknown): ClientError {
  return new ClientError(typeof value === "string" && hostErrors.has(value) ? value : "BACKEND_IO_ERROR",
    "Application bridge unavailable");
}
