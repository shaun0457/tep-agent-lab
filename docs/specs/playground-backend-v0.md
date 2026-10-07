# Playground Backend / Canonical Context v0

Status: proposal  
Owner repo: `tep-agent-lab`  
Depends on: `industrial-agent-runtime`, `tep-sim`, existing lab investigation/tool contracts

## Goal

Define the minimum application/backend contract required to host one reproducible Industrial Agent Playground run without creating a second Agent runtime, second canonical investigation state, or second process-world truth store.

P0 is intentionally local-first and transport-neutral. It provides lifecycle, manifest, context-source resolution, read projections, event projection, and artifact access for one run. HTTP, streaming, distributed execution, and a polished UI are later adapters/consumers.

## Layering

```text
User / Researcher / future UI
            |
            v
Playground Application Plane
  RunManager / RunManifest
  CanonicalContextRegistry
  read + event projections
  artifact access
            |
            v
industrial-agent-runtime
 Coordinator / gates / Executor / verifier / trace
            |
            v
tep-agent-lab
 RcaState / Tool Surface / Tool Bridge / RunLog
            |
            v
tep-sim
 ProcessGraph / Environment / snapshot/fork/safety
```

The Application Plane is not a fourth core repository in v0. Initial P0 implementation lives in `tep-agent-lab` because that repo is the integration point that intentionally knows both generic runtime contracts and TEP domain semantics.

## Authority boundary

Application/UI API and Agent Tool Surface are distinct.

An application call may create/prepare/start a run or query a read projection. It does not itself grant a model authority to execute a tool.

Agent tool execution still follows the existing authority path:

```text
ToolSpec
 -> runtime pre-execution gates
 -> lab consumer validate_request
 -> Executor
 -> provider/world implementation
 -> runtime post-execution verification
 -> deterministic ingestion
```

The backend MUST NOT add a shortcut such as `ui_run_rollout()` that mutates or advances Agent-accessible world state outside the normal ToolSpec/gate/verifier path.

Trusted benchmark-harness actions used to construct a case remain evaluator/application setup, not Agent tools, and must be kept out of Agent-visible projections unless the benchmark visibility policy explicitly allows them.

## Canonical state ownership

P0 assembles and projects existing canonical owners; it does not replace them.

Canonical owners remain:

- `TEPEnvironment` / `ReferenceWorld` / `SimulationSandbox` for process-world state and branch lifecycle;
- runtime `TraceRecorder` for runtime control-plane trace;
- lab `RcaStateStore` / `RunLog` for investigation state and typed investigation events;
- lab `ArtifactStore` and typed refs/checksums for Agent-visible artifacts;
- ProcessGraph/registries/rules in their owning source contracts.

P0 MUST NOT introduce a new `PlaygroundState` that duplicates hypotheses, evidence, world state, tool trace, or branch truth.

Materialized application views are derived projections and may be rebuilt from their sources.

## Run lifecycle

### RunStatus v0

`RunStatus` is an **application-hosting lifecycle**, not runtime `TaskStatus`. It must not replace, mirror-write, or become the canonical task-state status owned by the runtime/consumer store. Terminal `COMPLETED`/`FAILED` semantics are defined under `RunOutcome`.

P0 defines only:

```text
CREATED
  -> READY
  -> RUNNING
       -> COMPLETED
       -> FAILED
```

Rules:

- `create()` produces `CREATED` only;
- `prepare()` is atomic with respect to readiness: it either resolves/attests all required sources and publishes the immutable manifest, transitioning `CREATED -> READY`, or fails without publishing a partial manifest and leaves the run `CREATED`;
- `start()` is accepted only from `READY`, transitions once to `RUNNING`, and gives exactly one application execution owner responsibility for one Coordinator run;
- concurrent/duplicate `start()` attempts for the same run fail deterministically;
- after execution begins, the application records `RUNNING -> COMPLETED` or `RUNNING -> FAILED` from the execution outcome;
- terminal states do not transition back to `RUNNING`;
- `PAUSED`, `CANCELLED`, checkpoint/resume, and distributed worker states are not part of v0;
- adding interruption semantics later requires an explicit runtime/application contract rather than a UI-only flag.

Whether a transport adapter invokes `start()` synchronously or asynchronously is not part of the P0 semantic contract. The invariant is one active execution owner, not a second scheduler.

## RunManifest and terminal outcome

### RunManifest

`RunManifest` is published atomically when the run becomes `READY` and is immutable thereafter. It is the reproducibility root describing **what was prepared to run**, not a mutable status record.

Conceptual fields:

```text
RunManifest
  run_id
  created_at
  manifest_version

  source_revisions
    tep_sim_git_revision
    industrial_agent_runtime_git_revision
    tep_agent_lab_git_revision

  numerical_stack
    dependency/version records

  world
    environment_version
    upstream_revision
    environment_config_checksum
    deterministic_seed

  process_semantics
    fixture_id
    fixture_version
    fixture_checksum
    review_status

  runtime_policy
    tool_set_version
    gate_policy_version
    visibility_policy_version

  model
    provider
    model_name
    model_version
    model_config_ref/checksum
    prompt/template version or ref

  benchmark
    benchmark_case_ref?
    evaluator_ground_truth_ref?

  canonical_context_sources[]
  artifact/run storage refs
```

Required properties:

1. exact repo/context revisions are identifiable;
2. numerical environment is identifiable;
3. world/process/tool/policy/model versions are identifiable;
4. hidden evaluator truth is represented only by visibility-controlled refs, never copied into AGENT-visible manifest projections;
5. secrets, API tokens, deploy keys, credentials, and raw private keys never appear in the manifest;
6. mutating a source after a run cannot silently change what version the manifest claims the run used;
7. the canonical-context source inventory used by the run is frozen with the manifest at `READY`; changing that inventory requires preparing a different run/manifest rather than mutating the ready run.

The canonical/internal manifest is application/evaluator-owned metadata. It is not automatically an AGENT-visible payload. `RunSummaryView` and `ContextInventoryView` provide visibility-filtered projections; source ids/paths/refs that would reveal hidden truth must be withheld.

### RunOutcome

Terminal execution metadata is stored separately rather than mutating `RunManifest`.

Conceptual record:

```text
RunOutcome
  run_id
  terminal_status     # COMPLETED | FAILED
  started_at
  finished_at
  runtime_result_ref?
  failure_ref_or_summary?
  provenance
```

Terminal status semantics (normative):

- `COMPLETED` means the accepted execution owner invoked the Coordinator, obtained a valid `RuntimeResult`, and constructed the terminal application outcome.
- The runtime task terminal status (`TaskStatus` DONE/FAILED/EXHAUSTED/CANCELLED) is stored and reported separately, in the runtime result and the runtime/consumer task state. It is never mirror-written into `RunStatus`.
- A runtime `FAILED` or `EXHAUSTED` task does not imply application `FAILED`. `COMPLETED + DONE`, `COMPLETED + FAILED` and `COMPLETED + EXHAUSTED` are valid, distinct combinations.
- `FAILED` represents a hosting/execution-path failure before a valid `RuntimeResult`/terminal outcome exists (for example, the Coordinator raises or outcome construction fails). It is not a task-outcome mapping.

Application `COMPLETED` therefore answers "did the Playground successfully host and record the execution?", not "did the Agent task succeed?".

Outcome/error projections remain visibility-aware. A hidden evaluator/setup error must not become an Agent-side oracle merely because it is useful for debugging.

## RunSession

`RunSession` is an ephemeral in-process assembly object. It is not durable canonical state and Python object identity is never a reproducibility contract.

A session may hold handles to:

- `ReferenceWorld`;
- `RcaStateStore`;
- `RunLog`;
- `ArtifactStore`;
- `BlindRcaToolSurface` and later C5 bridge tools;
- runtime `Coordinator`;
- provider;
- `TraceRecorder`;
- benchmark/evaluator helpers kept outside Agent-visible views.

Durability uses manifests, typed records, exact refs, source revisions and artifacts rather than pickling the entire assembled Python object graph.

## RunManager

P0 defines a small transport-neutral lifecycle owner.

Conceptual operations:

```text
create(...)
prepare(run_id, ...)
start(run_id)
get(run_id)
```

Exact method names are implementation details, but responsibilities are fixed:

- assign/validate run identity;
- resolve and attest exact canonical-context sources during `prepare`;
- assemble pinned world/runtime/lab components;
- freeze the source inventory and atomically publish one immutable `RunManifest` when entering `READY`;
- validate lifecycle transitions and reject duplicate execution ownership;
- execute through the existing runtime Coordinator/provider path;
- persist a separate `RunOutcome` for terminal completion/failure;
- clean up owned resources deterministically;
- expose read-only run/query services.

`RunManager` does not authorize individual model tool calls, does not duplicate B2/B3, and is not a second task scheduler.

## Canonical long-term context

### Principle

Reviewed/versioned engineering or research truth belongs in repository-controlled canonical sources and is materialized locally at exact revisions for reproducible runs.

The local checkout is a persistent context substrate, not automatically model-visible context.

```text
Git repository canonical source
        |
        | exact revision / local checkout
        v
CanonicalContextRegistry
        |
 visibility + governance + provenance
        |
        v
bounded resolver/projection
        |
        v
ContextProjection / application view
```

Run-specific mutable state does not become canonical long-term context merely because it is useful during one investigation.

### ContextSourceRef

Conceptual contract:

```text
ContextSourceRef
  source_id
  repository
  git_revision
  path_or_ref
  content_checksum
  kind
  schema_version
  visibility
  provenance?
  governance?
```

A source ref MUST identify immutable content strongly enough that a run can later determine exactly what was used.

`governance` is source-kind-specific metadata, not a universal Rule authority field. When the owning source contract defines `origin × validation × authority` (for example an ENGINEERING_RULE or policy rule), those semantics may be carried without reinterpretation. ProcessGraph fixtures, benchmark cases, scoring policy, and evaluator ground truth are not forced into Rule Registry authority categories merely because they are canonical sources.

Repository + Git revision alone may be insufficient for generated/materialized content; in that case include a content checksum/ref as well.

### Candidate source kinds

The registry supports an extensible kind identifier. Initial examples:

```text
PROCESS_GRAPH
VARIABLE_REGISTRY
BINDING_REGISTRY
SAFETY_RULES
CAPABILITY_REGISTRY
SCENARIO_MAPPING
ENGINEERING_RULE
BENCHMARK_CASE
EVALUATOR_GROUND_TRUTH
VISIBILITY_POLICY
SCORING_POLICY
```

This list is illustrative rather than a permanently closed enum.

### Visibility and projection scope

P0 requires at least logical projection scopes:

```text
AGENT
EVALUATOR
```

These are application projection scopes. Source/ref visibility should reuse existing generic visibility semantics where compatible rather than inventing an incompatible second runtime visibility model.

P0 must guarantee that evaluator-only source material cannot be resolved through AGENT views. Local materialization NEVER implies Agent visibility.

### CanonicalContextRegistry

The registry is trusted application/harness state, never model-authored state.

Responsibilities:

- register immutable `ContextSourceRef`s during run preparation;
- validate exact source revision/ref/checksum where applicable;
- resolve sources deterministically;
- enforce caller/view visibility;
- expose a visibility-filtered source inventory;
- provide stable refs from which `ContextProjection` or application views may be built;
- freeze the run's source inventory at `READY` together with the manifest.

After `READY`, the Agent/model cannot add, remove, replace, or relabel canonical sources for that run. Run-generated observations/results remain run state; they do not become new canonical-context sources automatically.

The registry is NOT:

- chat memory;
- model memory;
- a vector database;
- an embedding store;
- a RAG framework;
- a replacement ProcessGraph;
- a replacement Rule Registry;
- a replacement TaskStateStore;
- automatic injection of all repository content into prompts.

P0 requires deterministic direct resolution first. Search/retrieval/indexing may be added later only under an explicit need and visibility/provenance contract.

## Git-backed canonical truth versus run-specific state

Examples expected to be repository/version controlled:

- ProcessGraph fixture and source provenance;
- XMEAS/XMV variable metadata;
- reviewed semantic bindings;
- safety limits;
- scenario/capability mappings;
- reviewed engineering rules;
- benchmark case manifests;
- visibility/scoring policies;
- evaluator ground-truth fixture records.

Examples expected to remain run-specific:

- current hypotheses/ranks;
- current observations;
- evidence links;
- experiment interpretations;
- temporary simulation branches/snapshots;
- current tool/model budget usage;
- trace events;
- temporary telemetry/artifacts produced by the run.

Run-specific results may later be reviewed/promoted into a separate canonical source through an explicit governance workflow, but never automatically.

## Read projections

P0 exposes transport-neutral derived views.

### RunSummaryView

Contains application `RunStatus`, manifest ref/version, task/investigation identifiers, high-level resource state, and terminal `RunOutcome` information where visible. It does not replace runtime `TaskStatus`.

### ProcessGraphView

P&ID-like process/topology projection built from normalized ProcessGraph and allowed metadata.

It may include equipment/stream topology, visible measurements/actuators and engineering provenance. It must not expose evaluator-only disturbance/fault mappings in blind mode.

This is the backend surface a later UI may use for a 2D process/P&ID-like display.

### TelemetryView

Bounded current/history telemetry projection using existing sanitized world/lab contracts. Dense data remains artifact-backed.

### InvestigationView

Derived from `RcaStateStore` and typed investigation records. Observation/Evidence semantics remain unchanged.

### BranchTreeView

Projects visible simulation branch/snapshot lineage and status from `SimulationSandbox`/world records without exposing raw filesystem paths, hidden random state, evaluator-only cause truth, or other hidden fields.

### BudgetView

Projects runtime budget limits/usage/reservations at a level allowed by the current caller/view policy.

### RunEventView

Provides a chronological UI-oriented projection across existing event sources while preserving source identity/provenance.

### ArtifactView

Provides typed metadata and content access through exact refs/checksums subject to visibility.

### ContextInventoryView

Lists only canonical context sources visible to the caller/view. An AGENT inventory must not reveal evaluator-only source identifiers, filenames, paths, descriptions, or counts that themselves leak hidden benchmark truth.

## Event projection

The Playground MUST preserve:

```text
Trace != Observation != Evidence
```

P0 does not create a new universal canonical event ontology.

`RunEventView` may normalize existing runtime trace and lab run-log records for UI consumption, but each projected event must retain enough metadata to identify:

- original source (`runtime_trace`, `lab_run_log`, world/application lifecycle, etc.);
- original type/status;
- source ref or source event id when available;
- timestamp/order semantics;
- relevant visibility/provenance.

The projected event feed is a view and is never itself engineering evidence unless an existing typed observation/evidence contract says so.

No Kafka/event-bus dependency is required in P0.

## Artifact access

Rules:

- exact typed ref required;
- checksum verification required where the owning artifact contract provides it;
- visibility checked before resolution;
- arbitrary caller-supplied filesystem paths are never accepted;
- raw private world artifact paths do not become Agent-visible API payloads;
- dense outputs remain artifact-backed rather than dumped into model context.

The backend may expose a transport-neutral `get_artifact(ref)` style service. HTTP download endpoints are a later adapter.


## D0 benchmark binding

The concrete D0 benchmark fixture/scorer contract is owned by
`benchmark-case-v0.md`. P0 remains the run/provenance owner and MUST NOT gain a
second benchmark execution path.

For benchmark runs, the existing trusted `BenchmarkRefs` binding is extended to
carry immutable benchmark identity/version/partition, exact EVALUATOR-only case
and ground-truth source ids, Agent projection checksum, and policy/scorer versions.

The internal `RunManifest.benchmark` records those values plus the typed
`CaseSetupAttestation` produced by trusted benchmark setup.

Rules:

- both benchmark fixture and evaluator ground-truth sources are EVALUATOR-only;
- benchmark READY requires a valid setup attestation;
- failed/missing benchmark setup attestation aborts preparation atomically;
- `RunManifest.agent_projection()` excludes the benchmark/internal section;
- AGENT context/source inventory must not reveal hidden source ids, paths or counts;
- non-benchmark developer/demo runs may retain the current empty refs and boolean-only
  `case_setup_applied` behavior;
- benchmark harness/projection/scorer live in the lab/evaluation layer, not in generic
  runtime authority or simulator internals.

P0 may persist the typed setup attestation in the internal manifest because the
internal manifest is trusted/EVALUATOR state. It must not copy the raw hidden
setup into Agent-visible task metadata or artifacts.

## Blind versus evaluator projections

### AGENT / blind

Default while observing an Agent investigation.

May contain only information allowed under the benchmark/tool/context visibility policy.

Must not include:

- injected fault/IDV id unless explicitly visible by benchmark design;
- evaluator candidate cause set;
- hidden injection parameters/timing if hidden;
- evaluator-only semantic bindings;
- hidden source paths/ref names that reveal the answer.

### EVALUATOR

Explicit trusted view for benchmark/debug/scoring use.

May resolve evaluator-only sources needed to create/score the case.

Evaluator scope is never implicitly inherited by Agent `ContextProjection` construction.

## ContextProjection relationship

`CanonicalContextRegistry` does not replace runtime `ContextProjection`.

The registry answers:

> What canonical sources are available to this run, at exactly what version, governance/provenance and visibility?

`ContextProjection` answers:

> What bounded information does this model turn receive now?

A model turn never receives the entire local repo checkout merely because the registry knows where it is.

## Run reproducibility

A completed or failed execution should be inspectable from:

```text
RunManifest
+ RunOutcome
+ exact repository/context sources
+ runtime TraceRecorder
+ lab RunLog/RcaState records
+ typed artifacts
+ world/run provenance
```

This is post-run reproducibility/auditability, not a promise that an external nondeterministic LLM provider will produce bit-identical outputs unless provider/model conditions actually guarantee that.

## Transport boundary

P0 is transport-neutral.

No mandatory framework is selected for:

- HTTP;
- REST;
- SSE/WebSocket;
- RPC;
- database;
- event bus.

A later P1 UI may add thin HTTP + streaming adapters over the same `RunManager` and query contracts.

Transport adapters do not become authority bypasses.

## Persistence boundary

P0 may use current local filesystem/run-log/artifact mechanisms. It does not require a new database.

If SQLite/DuckDB or another index is later introduced for convenience, it is a materialized/indexed view unless explicitly promoted by a new ownership contract.

Do not duplicate canonical data into a database and silently make the database the source of truth.

## Explicit non-goals

P0 does not include:

- production multi-user authentication/authorization;
- cloud tenancy;
- distributed simulation workers;
- generic job queue;
- microservice decomposition;
- Postgres/Redis/Kafka requirement;
- Kubernetes deployment;
- pause/resume/cancel semantics;
- UI-triggered arbitrary reference-world mutation;
- UI as Agent tool-authority bypass;
- vector DB / embeddings / RAG framework;
- cross-run learned model memory;
- full P&ID editing;
- generic P&ID OCR;
- 3D plant rendering;
- arbitrary filesystem browser;
- automatic promotion of run outputs into canonical engineering truth.

## Future P1 UI contract direction

A later UI should consume P0 projections rather than direct internal Python objects.

Initial panels may include:

```text
Process / P&ID-like view
Telemetry / safety
Agent investigation state
Simulation branch tree
Run/tool/budget event timeline
```

Flow visualization must distinguish direct measured/bound flow values from topology-only direction. The UI must not invent dynamic pipe-flow physics where the world has no corresponding runtime variable.

## Acceptance criteria for future P0 implementation

1. `prepare()` atomically resolves/attests the pinned world/runtime/lab/context sources, freezes the context-source inventory, publishes one immutable `RunManifest`, and transitions `CREATED -> READY`; failed preparation leaves no partial manifest and no READY state.
2. Illegal lifecycle transitions and duplicate/concurrent `start()` attempts fail deterministically.
3. One accepted `start()` owns one Coordinator execution; a fake-provider run executes through Coordinator, B2 gates, consumer validation, Executor and B3 verifier rather than a backend shortcut or second scheduler.
4. Terminal execution writes a separate `RunOutcome`; `RunManifest` remains unchanged after READY.
5. ProcessGraph, telemetry, investigation state, branch tree, runtime budget/resource usage, run events and artifacts are queryable as read projections.
6. `CanonicalContextRegistry` resolves exact pinned source refs deterministically and detects revision/checksum mismatch.
7. The registry/source inventory is trusted application state, frozen at READY, and cannot be model-authored or mutated by the Agent during the run.
8. AGENT resolution/view cannot access EVALUATOR-only truth even when evaluator sources are locally materialized.
9. Evaluator projection is explicit and cannot silently enter Agent `ContextProjection`/tool results.
10. The canonical/internal manifest is not exposed raw to AGENT scope when it contains evaluator-only refs; AGENT manifest/context views are visibility-filtered.
11. Entire repository contents are never automatically injected into `ContextProjection`.
12. Agent tool calls continue through existing runtime authority contracts.
13. Application `RunStatus` and runtime/consumer `TaskStatus` remain distinct ownership domains.
14. Runtime Trace, Observation and Evidence semantics remain distinct through application projections.
15. Artifact access uses exact typed refs/checksums and exposes no arbitrary raw paths.
16. A completed/failed execution retains enough manifest/outcome/source/provenance/artifact information for deterministic post-run inspection of the engineering/runtime state that was used.
17. No mandatory distributed/database/RAG infrastructure is required to pass P0 acceptance.
18. Application/backend projections do not become a second `TaskStateStore`, ProcessGraph, or process-world truth store.
19. Context-source inventory itself is visibility-safe and cannot leak hidden benchmark truth via evaluator-only ids/paths/counts.
20. P&ID-like ProcessGraph projection contains only semantics available to its visibility scope and preserves source/review provenance.

## Related decisions

Program Decision Register:

- D-020 Information Plane is logical, not mandatory storage-service decomposition;
- D-021 generic runtime vs domain ownership;
- D-044 Application / Playground Plane;
- D-045 Application/UI vs Agent tool authority;
- D-046 P0 required before D0 freeze;
- D-047 local-first/single-process P0;
- D-050 repository-backed canonical context and visibility-aware local materialization.

## Implementation gate

This spec is documentation-only until Program Re-baseline v1 is reviewed/merged.

Before P0 coding begins:

- B2.1/C5 may proceed independently according to their owning contracts;
- P0 implementation must pin the exact dependency heads it targets;
- A3 human-review status remains visible in process/topology provenance;
- benchmark-specific hidden-truth visibility remains owned by D0 benchmark policy rather than hard-coded into generic backend views.
