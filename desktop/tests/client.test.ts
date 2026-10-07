import { test } from "node:test";
import assert from "node:assert/strict";
import { createApplicationClient } from "../src/lib/applicationClient.ts";
import { PROTOCOL, validateEnvelope, safeHostError } from "../src/lib/protocol.ts";

test("four reads use only the narrow command and host-owned run identity", async () => {
  const calls: unknown[] = [];
  const client = createApplicationClient(async (command, args) => {
    calls.push([command, args]);
    return { protocol_version: PROTOCOL, request_id: `desktop-${calls.length}`, ok: true, result: {} };
  });
  await client.getRun(); await client.getEntity("reactor"); await client.getSignal("XMEAS(9)");
  await client.getSignalHistory("XMEAS(7)", { max_points: 30 });
  assert.deepEqual(calls, [
    ["application_request", { method: "get_run", params: {} }],
    ["application_request", { method: "get_entity", params: { entity_id: "reactor" } }],
    ["application_request", { method: "get_signal", params: { signal_id: "XMEAS(9)" } }],
    ["application_request", { method: "get_signal_history", params: { signal_id: "XMEAS(7)", max_points: 30 } }],
  ]);
});

test("malformed envelopes and unsafe errors fail closed", () => {
  const good = { protocol_version: PROTOCOL, request_id: "desktop-1", ok: true, result: {} };
  assert.equal(validateEnvelope(good).ok, true);
  for (const value of [null, {}, { ...good, protocol_version: "other" }, { ...good, request_id: null },
    { ...good, ok: 1 }, { ...good, result: null }, { ...good, result: { value: Infinity } },
    { ...good, error: null }, { ...good, result: [1] },
    { protocol_version: PROTOCOL, request_id: "desktop-1", ok: false, error: { code: "INTERNAL_ERROR", message: "secret path" } }]) {
    assert.throws(() => validateEnvelope(value), /Invalid application response/);
  }
  assert.equal(safeHostError("private path").message, "Application bridge unavailable");
  assert.equal(safeHostError("BACKEND_EXITED").code, "BACKEND_EXITED");
});

test("application failures are safe, surfaced and do not masquerade as success", async () => {
  const client = createApplicationClient(async () => ({ protocol_version: PROTOCOL, request_id: "desktop-1", ok: false,
    error: { code: "UNKNOWN_ENTITY", message: "unknown entity" } }));
  await assert.rejects(client.getEntity("missing"), { code: "UNKNOWN_ENTITY", message: "unknown entity" });
  const failed = createApplicationClient(async () => { throw new Error("sensitive stderr"); });
  await assert.rejects(failed.getRun(), { code: "BACKEND_IO_ERROR", message: "Application bridge unavailable" });
});
