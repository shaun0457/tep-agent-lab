# P0 Playground Backend handoff

- Branch: `feat/playground-backend-v0`; base: lab `main` `0fab21f72558506e100c4271bb860a502cfaf3b9`.
- Owning spec: `docs/specs/playground-backend-v0.md` (implementation gate satisfied: Program
  Re-baseline v1 is merged, see `docs/specs/README.md`).
- Exact dependencies (unchanged from the tep-sim 0.2.0 adoption):
  - `industrial-agent-runtime` = `92651cf305ad735871f26006a790f561fff82654`;
  - `tep-sim` = `ea0b7304090017b14ac13665595b6f5e9d195250` (package `0.2.0`);
  - ProcessGraph `tep-process-graph` `0.2.0`, sha256
    `cc8ccc81e9f421238863457438465877850b19d9760740279e54a52468fe9a87`, `HUMAN_VERIFIED`;
  - `numpy` = `2.4.6`. No new dependency.

## Implemented

`src/tep_agent_lab/canonical_context.py`

- `ProjectionScope` (`AGENT`, `EVALUATOR`), mapped onto the runtime `Visibility` values.
- `ContextSourceRef`: frozen; exact 40-hex revision, repository-relative POSIX path (no
  absolute, parent, drive or backslash paths), content checksum, kind, schema version,
  visibility, source-kind-specific `provenance`/`governance`.
- `CanonicalContextRegistry`: trusted run-scoped inventory. `register` attests the
  attested repository revision and the content checksum; `freeze` at READY; `resolve`
  checks visibility first and re-attests content each time. AGENT scope raises the same
  `UnknownContextSource` for unknown and hidden ids.
- Materializers: `PackageSourceMaterializer` (pinned package `src/<pkg>/...` files) and
  `DirectorySourceMaterializer` (local checkout).

`src/tep_agent_lab/playground.py`

- `RunStatus` `CREATED -> READY -> RUNNING -> COMPLETED | FAILED`, separate from runtime
  `TaskStatus`.
- `RunManager.create / prepare / start / get / manifest / queries`.
  - `prepare` attests dependency revisions against `dependency-pins.json` and NumPy, then
    registers and attests every source. It builds the world from the attested
    ProcessGraph content, runs the trusted harness `case_setup`, assembles the session,
    freezes the inventory, scans the manifest for credentials, and publishes the
    manifest atomically (temp + fsync + rename) into a per-attempt directory. Any
    failure tears the session down, publishes no manifest, records `PREPARE_FAILED`, and
    leaves the run `CREATED`. A retry uses a fresh `prepare-NNNN` attempt. The failed
    attempt directory, including any harness world artifacts, is removed.
  - Credentials are screened in `RunRequest`/`ModelSpec` before anything is persisted,
    and in the manifest before it is published. Failure details are bounded and
    credential-screened.
  - `start` is accepted once, from READY only, under the manager lock with a durable
    `RUN_STARTED` record. Duplicate or concurrent starts raise `LifecycleError`. The single
    owner runs one `Coordinator.run()` with the existing gates (B2), lab consumer,
    Executor and verifier (B3), and `RcaResultIngestor`, then closes the session's
    simulator handles. The outcome is persisted before memory changes. A
    `KeyboardInterrupt`/`SystemExit` is recorded as `FAILED` and then re-raised.
- `RunManifest`: immutable; frozen sections; `agent_projection()` drops benchmark refs,
  storage layout and non-AGENT sources. `RunOutcome` is a separate `outcome.json`.
- The lifecycle is recorded in a lab `RunLog`. A fresh `RunManager` loads runs read-only
  and re-verifies the manifest and outcome checksums.

`src/tep_agent_lab/playground_views.py` (`RunQueries`, bound to one scope at
construction via `RunManager.queries(run_id, scope=AGENT)`; `get()`/`manifest()` are
trusted/EVALUATOR-only reads)

- `run_summary`, `manifest_view`, `context_inventory`, `process_graph`, `telemetry`,
  `investigation`, `branch_tree`, `budget`, `events`, `artifacts`, `get_artifact(ref)`.
- AGENT views pass the lab `leakage_findings` screen or raise `VisibilityViolation`.
  The AGENT manifest projection is an allowlist. The AGENT event feed omits failed
  prepare attempts and carries no payloads.
- Live-world views (`telemetry`, `branch_tree`) raise `ViewUnavailable` while RUNNING.
  v0 takes no world lock, and the execution owner mutates world/sandbox state.
- `get_artifact` accepts only an exact `InformationRef` that the run issued and the
  caller's scope can see, and verifies its checksum. Strings, mappings and paths are
  refused alike.

`src/tep_agent_lab/tep_world.py` (additive only): `SimulationSandbox.lineage_records()`,
`SimulationSandbox.close()` (idempotent; branches become `RELEASED`), and
retired-branch bookkeeping for the branch-tree view.
No existing behavior changed.

## Frozen P0 semantic decision

**Application COMPLETED != task success.** Decided by the architecture owner and now
normative in the spec (`RunOutcome`, terminal status semantics):

- `COMPLETED`: the execution owner's `Coordinator.run()` returned a valid `RuntimeResult`
  and the terminal application outcome was constructed, whatever the runtime
  `TaskStatus` (DONE/FAILED/EXHAUSTED/CANCELLED).
- `FAILED`: the hosting/execution path itself failed (the Coordinator raised, or the
  terminal outcome could not be constructed). It is not a task-outcome mapping.
- The task outcome is represented separately by runtime `TaskStatus`, reported beside the
  application status (`runtime_task_status`, `outcome.runtime_result.task_status`) and
  never mirrored into `RunStatus`.

Regression coverage uses the real Coordinator: budget exhaustion
(`COMPLETED + EXHAUSTED`) and the fail-closed token-metered path
(`COMPLETED + FAILED`, no exception), beside the existing raised-Coordinator test
(application `FAILED`). `RunManager.start()` needed no change.

## Interpretations (spec is conceptual here; not SPEC_CONFLICTs)

1. *(Resolved; see the frozen P0 semantic decision above.)*
2. **Revision attestation.** `SourceRevisions` is supplied by the trusted host. CI
   attests the dependency checkouts with `scripts/check.py`. `RunManager` additionally
   requires the runtime/tep-sim revisions to equal `dependency-pins.json` and NumPy to
   equal its pin. The lab revision comes from the host (the tests use `git rev-parse HEAD`).
3. **Checksums.** JSON sources use `sha256:canonical-json`, which does not depend on
   line endings (Windows checkouts have CRLF fixtures). `sha256:bytes` is available.
   The ProcessGraph is also cross-checked against tep-sim's own pinned content sha256
   and review status.
4. **AGENT manifest checksum.** The AGENT summary carries the checksum of the AGENT
   projection, not of the internal manifest. Otherwise hidden benchmark refs could be
   guessed offline against that checksum.
5. **Ephemeral sessions.** READY sessions are not resumable across processes; a loaded
   READY run cannot `start`. Telemetry and branch-tree views need the in-process session.
   They keep working after terminal cleanup, but are `ViewUnavailable` after a reload.
   Manifest, outcome, trace, RcaState, artifacts and events are durable. The
   ProcessGraph view is rebuilt from re-attested sources.
6. **EVALUATOR scope** can also see INTERNAL runtime artifacts (persisted
   `ContextProjection`s) by exact ref.

## Not implemented (out of P0 scope)

D0 benchmark/scoring, real LLM provider/B5, frontend/UI, HTTP/FastAPI, MCP, telemetry
ingestion, knowledge/RAG/vector DB, OPC-UA/MES/ERP connectors, generic product
abstraction, F-11, pause/resume/cancel, distributed execution.

## Verification

```powershell
py -3.13 scripts/check.py --runtime ../industrial-agent-runtime --tep-sim ../tep-sim
```

160 tests (117 existing regressions + 43 P0) pass locally on Python 3.13 against the exact
pins. CI runs the same on 3.11 and 3.13. The P0 acceptance coverage in
`tests/test_playground.py` includes:

- atomic/retryable prepare;
- revision, checksum, dependency-pin, NumPy and credential rejections;
- illegal transitions;
- a concurrent `start` rejected by the RUNNING owner check (the owner is held inside
  `Coordinator.run()`);
- an interrupt recorded as `FAILED`;
- credential redaction in failure details;
- rejection of AGENT sources that alias hidden content;
- a FAILED outcome without an AGENT oracle;
- `COMPLETED` hosting with runtime `EXHAUSTED` and runtime `FAILED` task results;
- one fake-provider run through Coordinator/B2/consumer/Executor/B3 (4 observations,
  0 evidence links);
- the manifest unchanged after READY, with the outcome kept separate;
- manifest versions equal to the traced tool-set/model identity;
- a frozen, non-model-authorable inventory;
- AGENT/EVALUATOR filtering of every view;
- exact-ref artifact access;
- no registry or evaluator material in any persisted `ContextProjection`;
- post-run inspection from a fresh manager;
- detection of post-run source mutation and manifest tampering.

## Follow-ups

- Telemetry/branch-tree durability after reload would need a world-owned durable lineage
  and history record (tep-sim/C4 ownership), not a Playground copy.
- The benchmark visibility policy for world fields such as `deterministic_seed` in AGENT
  projections is owned by D0 and is currently visible.
- A transport adapter (P1) should wrap `RunManager`/`RunQueries` without adding authority.

## Review

code-reviewer, python-reviewer and security-reviewer passes, then the built-in `code-review` (high), ran before the PR. Fixed:

- event-feed order (`RUN_STARTED` before trace);
- durable-first outcome and `BaseException` handling in `start`/`prepare`;
- `RELEASED` branch status after cleanup;
- no live-world reads while RUNNING, and tolerance of a torn trace line;
- scope-bound queries and the allowlisted AGENT manifest projection;
- request/failure credential screening with broader patterns;
- canonical paths, reserved names, and AGENT-to-hidden source aliasing;
- `create` id burn, and write-once publication via hard link;
- test robustness: a deterministic RUNNING contender, timeouts, class cleanup,
  Windows path assertions, and no hard git dependency;
- read-only, torn-line-tolerant RcaState/lab-log reads in views;
- live-world views held under a per-run world lock that the execution owner holds
  for the whole run;
- AGENT lifecycle feed renumbered, so no sequence gap reveals a failed attempt;
- the alias check compares bytes and canonical JSON across paths and checksum
  methods;
- retired/released snapshot lineage stays connected;
- runs not owned by this process are re-read on every access;
- telemetry `current` honors the variable filter.

Accepted as follow-ups:

- runs loaded as CREATED after a restart cannot be prepared (the request object is not
  rebuilt from the log);
- `case_setup` content is attested only once D0 provides a benchmark case ref.
