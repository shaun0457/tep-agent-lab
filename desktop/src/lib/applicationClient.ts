import { invoke } from "@tauri-apps/api/core";
import { ClientError, safeHostError, validateEnvelope } from "./protocol.ts";
import type { JsonObject } from "./protocol.ts";

export type Method = "open_telemetry_snapshot" | "get_run" | "get_entity" | "get_signal" | "get_signal_history";
export type ApplicationRead = { requestId: string; result: JsonObject };
// The snapshot is opaque here: it is passed back verbatim and never interpreted (ADR-002).
export type TelemetrySnapshot = JsonObject;
export type SnapshotOptions = { event_horizon_ticks?: number; ingest_sequence?: number };
export type HistoryOptions = { event_start_ticks?: number | null; event_end_ticks?: number | null; max_points?: number };
type InvokeRead = (command: string, args: { method: Method; params: JsonObject }) => Promise<unknown>;

// One adapter owns IPC. Injection lets node:test verify the actual invocation boundary.
export function createApplicationClient(invokeRead: InvokeRead = invoke) {
  async function request(method: Method, params: JsonObject): Promise<ApplicationRead> {
    let raw: unknown;
    try { raw = await invokeRead("application_request", { method, params }); }
    catch (error: unknown) { throw safeHostError(error); }
    const response = validateEnvelope(raw);
    if (!response.ok) throw new ClientError(response.error.code, response.error.message);
    return { requestId: response.request_id, result: response.result };
  }
  return {
    openTelemetrySnapshot: (options: SnapshotOptions = {}) => request("open_telemetry_snapshot", { ...options }),
    getRun: (snapshot?: TelemetrySnapshot) => request("get_run", snapshot ? { snapshot } : {}),
    getEntity: (entityId: string) => request("get_entity", { entity_id: entityId }),
    getSignal: (signalId: string, snapshot: TelemetrySnapshot) =>
      request("get_signal", { signal_id: signalId, snapshot }),
    getSignalHistory: (signalId: string, snapshot: TelemetrySnapshot, options: HistoryOptions = { max_points: 30 }) =>
      request("get_signal_history", { signal_id: signalId, snapshot, ...options }),
  };
}

export const { openTelemetrySnapshot, getRun, getEntity, getSignal, getSignalHistory } = createApplicationClient();
