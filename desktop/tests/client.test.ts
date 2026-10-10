import { test } from "node:test";
import assert from "node:assert/strict";
import { createApplicationClient } from "../src/lib/applicationClient.ts";
import { PRESSURE_SIGNAL, TEMPERATURE_SIGNAL } from "../src/lib/probes.ts";
import { PROTOCOL, validateEnvelope, safeHostError } from "../src/lib/protocol.ts";

const snapshot = { snapshot_ref: "telemetry-snapshot:sha256:abc", ingest_sequence: 19, event_horizon_ticks: 1080 };

test("protocol is application transport v1", () => {
  assert.equal(PROTOCOL, "tep-agent-lab.application-transport/v1");
});

test("five reads use only the narrow command, host-owned run identity and a verbatim snapshot", async () => {
  const calls: unknown[] = [];
  const client = createApplicationClient(async (command, args) => {
    calls.push([command, args]);
    return { protocol_version: PROTOCOL, request_id: `desktop-${calls.length}`, ok: true, result: {} };
  });
  await client.openTelemetrySnapshot(); await client.getRun(snapshot); await client.getRun();
  await client.getEntity("reactor"); await client.getSignal(TEMPERATURE_SIGNAL, snapshot);
  await client.getSignalHistory(PRESSURE_SIGNAL, snapshot, { max_points: 30 });
  await client.openTelemetrySnapshot({ ingest_sequence: 0 });
  assert.deepEqual(calls, [
    ["application_request", { method: "open_telemetry_snapshot", params: {} }],
    ["application_request", { method: "get_run", params: { snapshot } }],
    ["application_request", { method: "get_run", params: {} }],
    ["application_request", { method: "get_entity", params: { entity_id: "reactor" } }],
    ["application_request", { method: "get_signal", params: { signal_id: TEMPERATURE_SIGNAL, snapshot } }],
    ["application_request", { method: "get_signal_history",
      params: { signal_id: PRESSURE_SIGNAL, snapshot, max_points: 30 } }],
    ["application_request", { method: "open_telemetry_snapshot", params: { ingest_sequence: 0 } }],
  ]);
  for (const call of calls) {
    const params = (call as [string, { params: Record<string, unknown> }])[1].params;
    assert.equal("run_id" in params, false);
  }
  assert.equal(TEMPERATURE_SIGNAL.includes("XMEAS"), false);
});

test("malformed envelopes and unsafe errors fail closed", () => {
  const good = { protocol_version: PROTOCOL, request_id: "desktop-1", ok: true, result: {} };
  assert.equal(validateEnvelope(good).ok, true);
  for (const value of [null, {}, { ...good, protocol_version: "other" },
    { ...good, protocol_version: "tep-agent-lab.application-transport/v0" }, { ...good, request_id: null },
    { ...good, ok: 1 }, { ...good, result: null }, { ...good, result: { value: Infinity } },
    { ...good, error: null }, { ...good, result: [1] },
    { protocol_version: PROTOCOL, request_id: "desktop-1", ok: false, error: { code: "INTERNAL_ERROR", message: "secret path" } },
    { protocol_version: PROTOCOL, request_id: "desktop-1", ok: false, error: { code: "SNAPSHOT_UNAVAILABLE", message: "K=19" } }]) {
    assert.throws(() => validateEnvelope(value), /Invalid application response/);
  }
  assert.equal(safeHostError("private path").message, "Application bridge unavailable");
  assert.equal(safeHostError("BACKEND_EXITED").code, "BACKEND_EXITED");
});

test("application failures, including the v1 telemetry codes, are safe and surfaced", async () => {
  for (const [code, message] of [["UNKNOWN_ENTITY", "unknown entity"], ["SNAPSHOT_UNAVAILABLE", "snapshot unavailable"],
    ["RESOURCE_LIMIT_EXCEEDED", "resource limit exceeded"]]) {
    const client = createApplicationClient(async () => ({ protocol_version: PROTOCOL, request_id: "desktop-1", ok: false,
      error: { code, message } }));
    await assert.rejects(client.getSignal(TEMPERATURE_SIGNAL, snapshot), { code, message });
  }
  const failed = createApplicationClient(async () => { throw new Error("sensitive stderr"); });
  await assert.rejects(failed.getRun(), { code: "BACKEND_IO_ERROR", message: "Application bridge unavailable" });
});
