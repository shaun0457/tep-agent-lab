# ADR-002 — Decouple the Observatory UI from the industrial backend

- Status: Accepted
- Date: 2026-10-06
- Scope: E0.2 / E1 UI architecture

## Context

E0 and E0.1 intentionally produce a self-contained HTML observatory. This is useful as a reproducible developer/research report, but it must not become the architectural boundary for future interactive UI.

The current industrial stack is already implemented in Python:

- P0 RunManager / RunQueries;
- RCA state and projections;
- ProcessGraph integration;
- telemetry/artifact access;
- Agent/runtime integration;
- TEP simulator integration;
- future Context Layer work.

Rewriting these responsibilities in Rust or Go merely to support a richer UI would create a second application/domain layer and force TypeScript/Rust/Go/Python cross-language synchronization without a demonstrated need.

## Decision

The UI is decoupled from industrial/domain retrieval through a transport-neutral Python application read boundary.

Target direction:

```text
                 UI clients
                     |
       +-------------+-------------+
       |             |             |
  Tauri desktop  future web   static E0 report
       |             |             |
       +-------------+-------------+
                     |
          Application View boundary
                     |
          ApplicationViewService
                  Python
                     |
                P0 RunQueries
          /          |          \
 ProcessGraphView TelemetryView ArtifactView
                     |
          runtime / lab / environment
```

### Frontend

The target interactive desktop UI is:

- Tauri v2;
- TypeScript;
- Vite;
- React when the UI requires component/state complexity.

The frontend owns:

- rendering;
- navigation;
- selection;
- transient interaction state;
- presentation formatting.

The frontend does not own:

- ProcessGraph truth;
- signal-to-entity bindings;
- telemetry retrieval semantics;
- artifact paths;
- simulator access;
- evaluator visibility policy;
- Agent/runtime business logic.

### Tauri / Rust boundary

Rust exists because Tauri uses a Rust host. It is initially a native shell/adapter, not the industrial backend.

Rust may own:

- desktop window lifecycle;
- secure OS integration;
- permissions;
- launching/stopping the local Python application service;
- narrow IPC/process supervision;
- packaging-related native integration.

Rust must not duplicate:

- P0;
- ProcessGraph semantics;
- telemetry query logic;
- Agent runtime;
- Context Layer;
- simulator/business rules.

A future Rust component requires an independently demonstrated ownership/performance/security need.

### Python application backend

Python remains the authoritative industrial application backend for the current system.

A transport-neutral `ApplicationViewService` provides bounded read models such as:

- Run;
- Entity;
- Signal;
- bounded SignalHistory.

It must consume public P0/application query contracts rather than directly reading simulator/private session state.

### Transport

E0.2A must not couple the service to HTTP, Tauri commands, or a specific IPC technology.

E0.2B introduces a thin transport adapter after the service contract is stable.

The chosen local transport may later be HTTP, stdio/IPC, or a Tauri-managed sidecar boundary, but transport must remain replaceable and must not contain domain logic.

### Static E0/E0.1 report

The self-contained HTML observatory remains supported as a reproducible snapshot/report client.

It is no longer the intended architecture for interactive data retrieval.

### Go

Go is not introduced for E0.2/E1.

A future Go service may be appropriate for a distributed Agent Ops/control plane such as multi-tenant scheduling, deployment, fleet orchestration, or cloud APIs. That is a separate decision and requires demonstrated need.

## Milestones

```text
E0 / E0.1
Static reproducible observatory
        |
        v
E0.2A
Python ApplicationViewService
transport-neutral read boundary
        |
        v
E0.2B
Thin local transport adapter
no domain logic
        |
        v
E0.2C
Tauri v2 + TypeScript/Vite interactive client
Rust = shell/process/IPC adapter
        |
        v
E1
Interactive Industrial Observatory
        |
        +--> P&ID/process view
        +--> signal explorer
        +--> telemetry
        +--> run/Agent trace
        +--> branch/counterfactual views
        |
        v
later Context Layer views
SOP / manuals / incidents / asset context
```

## Consequences

Positive:

- UI technology can evolve without rewriting P0/domain logic.
- The static research report and interactive desktop UI can coexist.
- Python remains close to simulation/AI/scientific tooling.
- Tauri provides a path to a polished desktop product without making Rust the source of industrial truth.
- A second environment can later validate generic view contracts before broader platform extraction.

Costs:

- the desktop product will contain a TypeScript/Rust/Python process boundary;
- local service lifecycle and packaging must eventually be handled explicitly;
- API/read models must be versioned carefully.

These costs are accepted because they preserve clear ownership and avoid premature backend rewrites.

## Rejected alternatives

### Continue growing the self-contained HTML into the product UI

Rejected. It would preload domain state, bind interaction to report-generation internals, and make backend authorization/bounded retrieval difficult.

### Rewrite the industrial backend in Rust now

Rejected. No demonstrated bottleneck justifies reimplementing P0/runtime/simulation/domain contracts.

### Add a Go backend between UI and Python now

Rejected. It adds a pass-through service and a third application-language boundary without a clear owner.

## Invariants

- UI owns interaction, never industrial truth.
- ApplicationViewService owns bounded view assembly, not canonical domain state.
- P0 remains run/projection truth.
- ProcessGraph remains semantic/topology truth.
- Telemetry/artifacts remain dynamic-data truth.
- Rust shell must not become a second domain backend.
- Static reports remain reproducible/offline clients.
- Direct UI access to simulator/private session state is forbidden.
