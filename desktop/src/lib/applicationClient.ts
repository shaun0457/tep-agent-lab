import { invoke } from "@tauri-apps/api/core";
import { ClientError, safeHostError, validateEnvelope } from "./protocol.ts";
import type { JsonObject } from "./protocol.ts";

export type Method = "get_run" | "get_entity" | "get_signal" | "get_signal_history";
export type ApplicationRead = { requestId: string; result: JsonObject };
export type HistoryOptions = { start_hours?: number; end_hours?: number; max_points?: number };
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
    getRun: () => request("get_run", {}),
    getEntity: (entityId: string) => request("get_entity", { entity_id: entityId }),
    getSignal: (signalId: string) => request("get_signal", { signal_id: signalId }),
    getSignalHistory: (signalId: string, options: HistoryOptions = { max_points: 30 }) =>
      request("get_signal_history", { signal_id: signalId, ...options }),
  };
}

export const { getRun, getEntity, getSignal, getSignalHistory } = createApplicationClient();
