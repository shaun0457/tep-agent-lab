import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { getEntity, getRun, getSignal, getSignalHistory, openTelemetrySnapshot } from "./lib/applicationClient.ts";
import type { ApplicationRead, TelemetrySnapshot } from "./lib/applicationClient.ts";
import { HISTORY_POINTS, PRESSURE_SIGNAL, PROBE_ENTITY, TEMPERATURE_SIGNAL } from "./lib/probes.ts";
import { ClientError } from "./lib/protocol.ts";
import "./style.css";

type Probe = { title: string; query: string; read: (snapshot: TelemetrySnapshot) => Promise<ApplicationRead> };
const probes: Probe[] = [
  { title: "Run Summary", query: "get_run · pinned snapshot", read: (snapshot) => getRun(snapshot) },
  { title: "Entity probe", query: `get_entity · ${PROBE_ENTITY}`, read: () => getEntity(PROBE_ENTITY) },
  { title: "Signal probe", query: `get_signal · ${TEMPERATURE_SIGNAL} (XMEAS(9))`,
    read: (snapshot) => getSignal(TEMPERATURE_SIGNAL, snapshot) },
  { title: "Bounded history", query: `get_signal_history · ${PRESSURE_SIGNAL} (XMEAS(7)) · ≤${HISTORY_POINTS} points`,
    read: (snapshot) => getSignalHistory(PRESSURE_SIGNAL, snapshot, { max_points: HISTORY_POINTS }) },
];

// Presentation only: ticks times the snapshot clock's exact seconds-per-tick, as hours.
function hours(ticks: unknown, resolution: unknown): string {
  if (typeof ticks !== "number" || typeof resolution !== "string") return "Unavailable";
  const [numerator, denominator] = resolution.split("/").map(Number);
  if (!numerator || !denominator) return "Unavailable";
  return (ticks * numerator / denominator / 3600).toFixed(4);
}

function App() {
  const [snapshot, setSnapshot] = useState<TelemetrySnapshot | null>(null);
  const [run, setRun] = useState<ApplicationRead | null>(null);
  const [response, setResponse] = useState<ApplicationRead | null>(null);
  const [selected, setSelected] = useState(0);
  const [status, setStatus] = useState("Connecting to backend…");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ClientError | null>(null);

  function fail(failure: unknown) {
    const safe = failure instanceof ClientError ? failure : new ClientError("BACKEND_IO_ERROR", "Application request failed");
    setError(safe); setStatus("Request failed");
  }

  async function query(index: number, pinned: TelemetrySnapshot | null = snapshot) {
    if (!pinned) return;
    setSelected(index); setLoading(true); setError(null); setResponse(null);
    setStatus("Request in progress…");
    try {
      const data = await probes[index].read(pinned);
      if (index === 0) setRun(data);
      setResponse(data); setStatus(`Connected · ${data.requestId}`);
    } catch (failure: unknown) { fail(failure); } finally { setLoading(false); }
  }

  // The only place that chooses (T, K): every probe then reads under this exact snapshot.
  async function pin() {
    setLoading(true); setError(null); setStatus("Pinning telemetry snapshot…");
    try {
      const opened = await openTelemetrySnapshot();
      setSnapshot(opened.result);
      setLoading(false);
      await query(0, opened.result);
    } catch (failure: unknown) { fail(failure); setLoading(false); }
  }
  useEffect(() => { void pin(); }, []);

  return <main>
    <header><p className="eyebrow">Development Shell · P1.1C</p><h1>Industrial Agent Observatory</h1>
      <p className="status" role="status" aria-live="polite">{status}</p></header>
    <section className="card" aria-label="Telemetry snapshot"><h2>Telemetry snapshot</h2>
      {snapshot ? <dl><dt>Ingest revision (K)</dt><dd>{String(snapshot.ingest_sequence)}</dd>
        <dt>Event horizon (T, hours)</dt><dd>{hours(snapshot.event_horizon_ticks, snapshot.event_resolution)}</dd>
        <dt>Snapshot ref</dt><dd className="muted">{String(snapshot.snapshot_ref)}</dd></dl>
        : <p>No snapshot pinned.</p>}
      <button disabled={loading} onClick={() => { void pin(); }}><strong>New snapshot</strong>
        <span>Re-pin T and K explicitly; reads never follow the latest data on their own.</span></button>
    </section>
    <section className="card" aria-label="Run Summary"><h2>Run Summary</h2>
      {run ? <dl><dt>Run</dt><dd>{String(run.result.run_id ?? "Unavailable")}</dd>
        <dt>Status</dt><dd>{String(run.result.run_status ?? "Unavailable")}</dd>
        <dt>Latest event at or before T (hours)</dt>
        <dd>{hours(run.result.telemetry_event_ticks, snapshot?.event_resolution)}</dd></dl>
        : <p>Waiting for application summary.</p>}
    </section>
    <section className="card"><h2>Application probes</h2><div className="probes">
      {probes.map((probe, index) => <button key={probe.title} disabled={loading || !snapshot} aria-pressed={selected === index}
        onClick={() => { void query(index); }}><strong>{probe.title}</strong><span>{probe.query}</span></button>)}
    </div></section>
    {error && <section className="error" role="alert"><strong>{error.code}</strong><p>{error.message}</p></section>}
    {response && <section className="card"><h2>{probes[selected].title} response</h2><p className="muted">{response.requestId}</p>
      <pre>{JSON.stringify(response.result, null, 2)}</pre></section>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
