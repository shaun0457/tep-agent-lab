import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { getRun, getEntity, getSignal, getSignalHistory } from "./lib/applicationClient.ts";
import type { ApplicationRead } from "./lib/applicationClient.ts";
import { ClientError } from "./lib/protocol.ts";
import "./style.css";

const probes = [
  { title: "Run Summary", query: "get_run", read: getRun },
  { title: "Entity probe", query: "get_entity · reactor", read: () => getEntity("reactor") },
  { title: "Signal probe", query: "get_signal · XMEAS(9)", read: () => getSignal("XMEAS(9)") },
  { title: "Bounded history", query: "get_signal_history · XMEAS(7) · ≤30 points", read: () => getSignalHistory("XMEAS(7)", { max_points: 30 }) },
];

function App() {
  const [run, setRun] = useState<ApplicationRead | null>(null);
  const [response, setResponse] = useState<ApplicationRead | null>(null);
  const [selected, setSelected] = useState(0);
  const [status, setStatus] = useState("Connecting to backend…");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ClientError | null>(null);

  async function query(index: number) {
    setSelected(index); setLoading(true); setError(null); setResponse(null);
    setStatus("Request in progress…");
    try {
      const data = await probes[index].read();
      if (index === 0) setRun(data);
      setResponse(data); setStatus(`Connected · ${data.requestId}`);
    } catch (failure: unknown) {
      const safe = failure instanceof ClientError ? failure : new ClientError("BACKEND_IO_ERROR", "Application request failed");
      setError(safe); setStatus("Request failed");
    } finally { setLoading(false); }
  }
  useEffect(() => { void query(0); }, []);

  return <main>
    <header><p className="eyebrow">Development Shell · E0.2C2A</p><h1>Industrial Agent Observatory</h1>
      <p className="status" role="status" aria-live="polite">{status}</p></header>
    <section className="card" aria-label="Run Summary"><h2>Run Summary</h2>
      {run ? <dl><dt>Run</dt><dd>{String(run.result.run_id ?? "Unavailable")}</dd>
        <dt>Status</dt><dd>{String(run.result.run_status ?? "Unavailable")}</dd>
        <dt>Simulation time (hours)</dt><dd>{String(run.result.simulation_time_hours ?? "Unavailable")}</dd></dl>
        : <p>Waiting for application summary.</p>}
    </section>
    <section className="card"><h2>Application probes</h2><div className="probes">
      {probes.map((probe, index) => <button key={probe.title} disabled={loading} aria-pressed={selected === index}
        onClick={() => { void query(index); }}><strong>{probe.title}</strong><span>{probe.query}</span></button>)}
    </div></section>
    {error && <section className="error" role="alert"><strong>{error.code}</strong><p>{error.message}</p></section>}
    {response && <section className="card"><h2>{probes[selected].title} response</h2><p className="muted">{response.requestId}</p>
      <pre>{JSON.stringify(response.result, null, 2)}</pre></section>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
