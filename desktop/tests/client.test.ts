import { test } from "node:test";
import assert from "node:assert/strict";
import { createApplicationClient, isSnapshot } from "../src/lib/applicationClient.ts";
import { PRESSURE_SIGNAL, TEMPERATURE_SIGNAL } from "../src/lib/probes.ts";
import { PROTOCOL, validateEnvelope, safeHostError } from "../src/lib/protocol.ts";

const snapshot = {
  run_id: "e0-observatory", snapshot_ref: "telemetry-snapshot:sha256:abc", contract: "tep-agent-lab.telemetry/v0",
  store_id: "p0-reference-telemetry", incarnation: "inc-a", namespace: "p0-run:e0-observatory:a",
  ingest_sequence: 19, event_horizon_ticks: 1080, event_clock_id: "tep-sim:e0-observatory:a",
  event_clock_kind: "SIMULATION", event_resolution: "1/1", event_clock_origin: "tep-reset:e0-observatory:a",
  context_ref: "context-source:tep-sim.process-graph:sha256:b",
  visibility_policy_ref: "tep-agent-lab.playground-visibility/v0#AGENT",
};

test("snapshot guard is exact and read-only", async () => {
  assert.equal(isSnapshot(snapshot), true);
  const frozen = Object.freeze({ ...snapshot });
  assert.equal(isSnapshot(frozen), true);                       // never mutates its input
  for (const bad of [null, [], {}, { ...snapshot, extra: 1 }, { ...snapshot, ingest_sequence: -1 },
    { ...snapshot, ingest_sequence: 1.5 }, { ...snapshot, event_clock_origin: null },
    Object.fromEntries(Object.entries(snapshot).filter(([key]) => key !== "context_ref"))]) {
    assert.equal(isSnapshot(bad), false);
  }
  const reply = (result: unknown) => createApplicationClient(async () => (
    { protocol_version: PROTOCOL, request_id: "desktop-1", ok: true, result }));
  const opened = await reply(snapshot).openTelemetrySnapshot();
  assert.equal(opened.result.snapshot_ref, snapshot.snapshot_ref);
  await assert.rejects(reply({ ...snapshot, event_clock_origin: null }).openTelemetrySnapshot(),
    { code: "BACKEND_PROTOCOL_ERROR" });
});

test("protocol is application transport v1", () => {
  assert.equal(PROTOCOL, "tep-agent-lab.application-transport/v1");
});

test("five reads use only the narrow command, host-owned run identity and a verbatim snapshot", async () => {
  const calls: unknown[] = [];
  const client = createApplicationClient(async (command, args) => {
    calls.push([command, args]);
    const result = args.method === "open_telemetry_snapshot" ? snapshot : {};
    return { protocol_version: PROTOCOL, request_id: `desktop-${calls.length}`, ok: true, result };
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

// Regression for F9: extract the actual presentation regex, not a copied test regex.
test("presentation resolution grammar includes the Python 18-digit cap", async () => {
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(new URL("../src/main.tsx", import.meta.url), "utf8");
  const literal = source.match(/^const RESOLUTION = (.+);$/m)![1];
  const pattern = new RegExp(literal.slice(1, literal.lastIndexOf("/")));
  for (const value of ["1/1", "1/100000000000000000", "999999999999999999/1"]) {
    assert.equal(pattern.test(value), true, value);
  }
  for (const value of ["1/" + "9".repeat(19), "9".repeat(19) + "/1", "01/1", "1/01", "0/1", "1/0", "1/1\n", "1/1\r\n"]) {
    assert.equal(pattern.test(value), false, value);
  }
});

test("null run snapshot uses the same client request as an omitted snapshot", async () => {
  const calls: unknown[] = [];
  const client = createApplicationClient(async (_command, args) => {
    calls.push(args);
    return { protocol_version: PROTOCOL, request_id: "desktop-1", ok: true, result: {} };
  });
  await client.getRun();
  // Runtime null from JSON follows the same path even though the TS API uses optional.
  await client.getRun(null as unknown as typeof snapshot);
  assert.deepEqual(calls, [
    { method: "get_run", params: {} }, { method: "get_run", params: {} },
  ]);
});
