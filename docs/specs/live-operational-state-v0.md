# Live Operational State v0

Status: proposal

Version: v0

Owner repo: `tep-agent-lab`

Milestones: P1.4A (serving + Operational State), P1.4B (live plant mode), sequenced
by [ADR-004](../decisions/ADR-004-p1-milestone-rebaseline.md). Live plant scope and
constraints come from [ADR-005 §Live plant mode is MVP-required](../decisions/ADR-005-context-layer-product-positioning.md#live-plant-mode-is-mvp-required).

Depends on: [Plant Context + Telemetry v0](plant-telemetry-contract-v0.md),
[Playground backend v0](playground-backend-v0.md), [Application API v1](application-api-v1.md),
[Context Compiler v0](context-compiler-v0.md) and the existing RuleRegistry contract.
The [MVP guardrail](../product-maturity-and-mvp.md) supplies Q1–Q4 and exit criteria.

This proposal records the behavior selected in §Owner decisions (2026-10-12).
The owner accepts the spec through its PR merge. The companion
[Playground amendment](playground-backend-v0.md#live-plant-session-mode-amendment-2026-10-12)
introduces a distinct live mode and resolves SC-1/SC-2 without changing existing P0 runs.

## Purpose and scope

```text
[Trusted plant harness] -- sanitized observations --> [TEPSimulationSource]
[TEPSimulationSource] -- source batches --> [TelemetryIngestor / TimeSeriesStore]
[TimeSeriesStore] -- exact T/K reads --> [TimeSeriesReader / public P0 queries]
[Pinned plant + engineering context] -- scoped refs --> [State Interpreter]
[Public P0 queries] -- bounded canonical samples --> [State Interpreter]
[State Interpreter] -- typed Operational State --> [Application read assembly]
[Application read assembly] -- versioned read models --> [UI / later P1.5 consumer]
```

P1.4A answers Q2, “what is changing now?”, for one bounded subsystem: trend, rate
of change, threshold state, recent transition, staleness and related-signal changes.
It also retains Q1 current value/quality and bounded min/max. It serves deterministic
features with exact provenance; it does not diagnose a cause. Q3 contextualization
uses separately pinned plant/engineering refs. Q4 investigation remains consumer-owned.

P1.4B supplies a long-lived, clean deterministic simulated stream after preparation,
using the same canonical SignalSample/store/reader semantics as P1.1C. “Continuous”
means repeated stepping and delivery while the plant session is active, without an
investigation or UI read triggering each step. It does not mean infinite retention,
an unbounded request, or a real external plant connection.

Non-goals: full anomaly detection, predictive diagnosis, alarm acknowledgment or alarm
management, autonomous control, write-back or knowledge promotion, whole-factory
ontology, arbitrary engineering-document support, non-simulated connectors, IT-system
integration, enterprise identity/access management, high availability, multiple live
customer plants, production scalability and durable restart recovery. No database,
broker, external API, generic domain-pack interface or new runtime is selected.
Drawing extraction and plant-graph generation remain outside this milestone.

## Ownership and inherited invariants

| Concern | Owner / boundary |
|---|---|
| Physics, integration steps, reset, disturbance capability, simulator snapshots | `tep-sim`; imported public interfaces only |
| TEP bindings, grouping, relationships, supported disturbance translation | Process pack; pinned human-verified ProcessGraph and lab adapter |
| Sample acceptance, identity, commits, T/K reads | Plant telemetry contract and existing source/store/reader |
| Feature interpretation, policy versions, bounded derived results | State Interpreter defined here; read-only application/domain infrastructure |
| Static applicability, limits and citations | P1.3 composed view; P1.2 EngineeringContextRevision and existing RuleRegistry |
| Prepared source inventory and manifest | P0 CanonicalContextRegistry / RunManifest, frozen at READY |
| Plant delivery supervision | Trusted application/harness owner; distinct versioned live session lifecycle (OD-4) |
| Application projection / local transport | Public P0 queries -> ApplicationViewService -> versioned adapter |
| ContextSnapshot / investigation state / authority | Existing owning specs; P1.5 integration, unchanged runtime gates |

Generic feature contracts MUST NOT require XMEAS, XMV, IDV, ControlMode or TEP
imports. Signal/source ids are opaque. Numeric, boolean and string descriptors are
supported by explicit feature applicability; an unsupported feature returns a typed
status. TEP-specific policies and configuration belong in the process pack. No second
ProcessGraph, RuleRegistry, source inventory, world state or investigation state exists.

Both cutoffs and caller visibility MUST be applied before any selection, count,
feature, resource outcome or summary. Later commits, even with earlier event times,
cannot affect an earlier result. Current remains the last eligible sample in canonical
read order, whatever its quality; an older GOOD value is never substituted.

## Operational State contracts

Logical schemas below are proposed, not claims about existing Python/wire models.
Collections are immutable and deterministically ordered. All refs resolve exact
immutable contents or report unavailable. Values and source quality are preserved.

| Contract | Required contents |
|---|---|
| `InterpretationPolicyRef` | policy id, version, immutable content checksum; algorithm and numerical-method version; typed parameters and applicable value kinds/units |
| Read request | run id, exact TelemetryReadSnapshot, exact plant and selected engineering/rule-set refs (or explicit empty selection), visibility/selection policy refs, scoped signal/source selection, finite inclusive window, exact interpretation policy refs |
| Window ref | immutable query provenance: snapshot, binding, clock, inclusive bounds, read/reduction policy refs, coverage and exact input sample-selection artifact/checksum; not a context assembly |
| `SignalOperationalState` | schema version, run/source/signal identity, snapshot ref, context refs, resolved window and event clock, verbatim current sample/ref, feature results, quality/coverage diagnostics, request and derivation provenance |
| Feature result | typed value or null, status/reason, policy ref, contributing sample identities and window ref, source/context citations; no free-text judgment as the canonical value |
| `Transition` | feature kind, from/to typed states, exact before/after sample identities and event ticks, policy/limit refs, observation interval; optional elapsed time only when ticks differ |
| `RelatedSignalChange` | peer source/signal, exact relationship/grouping ref and selection policy, peer feature result refs at the same snapshot/window; no causal attribution |
| `LimitComparison` (derived) | exact RuleRef and canonical publication/scope/source refs; decoder policy ref; numeric lower/upper bounds (at least one), each boundary's inclusivity, exact unit and applicability status; never a new canonical Rule |

Feature status is one of `AVAILABLE`, `NO_DATA`, `INSUFFICIENT_DATA`,
`QUALITY_UNUSABLE`, `GAP_EXCEEDS_POLICY`, `UNSUPPORTED_VALUE_KIND`,
`NO_CANONICAL_LIMIT`, or `UNSUPPORTED_LIMIT_PREDICATE`. The last status retains the
canonical claim's exact citation and has no threshold value.
Missing required refs, inconsistent scope, unretained input,
reduced input or exceeded resource bounds fail the read; they are not successful
feature states. Staleness and source quality are separate axes, not upgrades or
replacements of these statuses. A result MAY have an available current value and
unavailable trend or threshold.
The public current sample retains the permitted value/quality/event/identity fields;
it does not serialize diagnostic ingest time or hidden source metadata.

Each result MUST retain:

- full telemetry snapshot identity (store incarnation/namespace, T, K, context and
  clock refs), exact selected binding and source, and the resolved window;
- identities `(source_id, signal_id, sequence)` for current and every sample in the
  complete feature window, including quality-rejected samples within that eligible
  window; no hidden or post-T/K record may enter derivation provenance;
- exact public reader query/result refs for current selection and its prefix count.
  The interpreter does not enumerate or inspect the prefix outside its feature
  window merely to reproduce a reader count;
- exact interpretation/request/selection versions, typed parameters, numeric units,
  reduction/coverage information, and all influencing static refs;
- a bounded immutable result artifact with checksum if inline provenance exceeds
  the response limit. Missing artifact resolution is unavailable, never recomputation
  from newer data.

These are derivation records, not a second context assembly identity. If persisted
assembly identity is needed, use the existing ContextSnapshot schema and semantics
(ADR-004 Decision 6); a feature artifact references that assembly rather than replacing it.

### Window selection and deterministic policy

The request MUST pin one snapshot and one finite window `[start, end]` with
`0 <= start <= end <= T` on its event clock. No interpreter uses implicit “now”,
wall-clock time or a fresh snapshot for a peer signal. Defaults selected under OD-2
are resolved once from the selected policy and T and returned in the result.
Current, threshold and freshness are explicitly as of T; trend, extrema and
transitions describe only the requested window. If end < T, the response retains
both coordinates and does not label window features as changes through T.

Read canonical current and history through the owning public query boundary. The
OD-1 policy requires an unreduced history: returned count equals eligible
count. Endpoint-index downsampling is valid for plotting but cannot prove min/max
or absence of a crossing. A reduced window fails `ResourceLimitExceeded`; it is not
silently interpreted as a complete window. A later aggregate reader would need its
own explicit bounded contract and version, not a private store scan.

The policy MUST declare maximum window ticks, input points per signal, aggregate
candidate/index work across the request, peers, transitions, inline refs and response
bytes. All bounds are finite and validated before work. There is no partial scan,
silent truncation or hidden peer influence. The underlying reader's bounds also apply,
including its prefix bound on current reads.

Implementing slices propose concrete values under OD-2, with a written rationale and
owner confirmation in each slice's PR. Values stay within existing reader bounds.

### Initial Q2 policies (OD-1)

This table specifies the owner-selected initial policy bundle. Alternative algorithms
require explicit owner selection and a new exact policy version; none is an implicit
fallback. Policies are pure functions of authorized canonical reads and pinned context.

| Feature | Deterministic rule / typed output |
|---|---|
| Current / quality | Canonical last sample at or before T under K, including null/BAD/UNCERTAIN; `NO_DATA` if absent. Keep exact quality and identity. |
| Rate of change | Numeric window only, excluding booleans. For the earliest and latest distinct event ticks, use the last canonical read-order sample at each tick. `rate = (last.value - first.value) / ((last.tick - first.tick) * clock.resolution_seconds)`, in signal unit/second. Fewer than two distinct ticks -> `INSUFFICIENT_DATA`; no division by zero. |
| Trend | `RISING` if rate > the policy's non-negative unit-specific tolerance, `FALLING` if rate < its negative, otherwise `STABLE`. This is endpoint trend over the stated window, not a monotonicity or anomaly claim. |
| Window min/max | Exact extrema of all numeric samples in the complete window. Ties cite the first sample in canonical read order. No reduced-window approximation. |
| Threshold state | Current GOOD non-null numeric sample evaluated against an applicable exact canonical limit: `BELOW`, `WITHIN`, `ABOVE`. Comparison uses the limit's explicit boundary inclusivity and unit. Without a canonical limit -> `NO_CANONICAL_LIMIT`; invalid/conflicting refs fail closed (§Operating limits). |
| Recent transition | Scan adjacent samples in canonical read order within the window for threshold-state changes (numeric) or exact value changes (boolean/string). Return ordered transitions, or `NONE_OBSERVED_IN_WINDOW` when the valid scan finds none. Cite both endpoints. No assertion about events before the window; no invented predecessor. |
| Staleness | `age_ticks = T - current.event_ticks`; `FRESH` if age <= pinned non-negative stale-after ticks, otherwise `STALE`; no sample -> `NO_DATA`. This is event-horizon freshness only. |
| Related-signal changes | Resolve peers from exact authorized plant relationships/grouping and the pinned bounded selection policy; sort by canonical source/signal id. Return their trend/rate/transition/staleness features at the same T/K/window, with independent statuses. No inferred correlation, lag, fault binding or causal direction. |

The numeric-method version declares arithmetic order, finite-value validation and
serialization precision; computations use unrounded input and exact clock resolution,
and display rounding does not feed classification. Nonfinite derived arithmetic fails
closed. Policy parameters such as trend tolerance are interpretation settings, not
engineering operating limits, safety gates or inferred canonical knowledge.
Threshold transitions use the same exact limit/comparison policy for both endpoints;
numeric transitions without a usable canonical limit have the threshold's unavailable
status. Boolean/string transitions compare values of the declared kind without a
numeric limit. A transition records an observed state change between samples, never
an inferred physical crossing instant. Its observation time is the later sample tick;
same-tick changes remain ordered by sequence.

### Quality, gaps and freshness (OD-1, OD-2)

The selected v0 policy is conservative: rate/trend/min/max require every sample in
their window to be GOOD, non-null and numeric. Otherwise they return the applicable
unusable/unsupported status with exact refs. Threshold examines current quality only;
a BAD current never becomes an older GOOD threshold state. Boolean/string transitions
need usable typed endpoints. No interpolation, imputation or quality upgrade is allowed.

Gap checks compare adjacent distinct event ticks to a declared maximum gap. They
cover the full requested history and disclose observed extent, leading/trailing
unobserved ticks, GOOD/UNCERTAIN/BAD/null counts and retained coverage. A span above
the gap bound returns `GAP_EXCEEDS_POLICY` for rate/trend and the transition scan;
an unusable sample makes the transition scan `QUALITY_UNUSABLE`. Neither produces
a partial transition list or an assertion of no transition.

Same-tick samples retain all identities. Endpoint rate selection is declared above;
transitions at equal ticks are observed ordered changes with zero event separation,
not a measurable rate or known physical crossing time.

`NONE_OBSERVED_IN_WINDOW` is available only for a usable scan; a one-point window
returns `INSUFFICIENT_DATA`. Full store coverage means retained accepted records,
not proof of a gap-free physical stream. Stale features retain their historical values
and an explicit STALE flag; they cannot be labeled current healthy conditions.

A paused plant may remain event-fresh if T is its last event. Wall-time delivery
inactivity is a separate application supervisor diagnostic, unavailable to the
deterministic feature function. No implicit simulation-to-UTC mapping is introduced.

The OD-1 policy evaluates each feature in this order: declared-kind
applicability, required canonical-limit availability where applicable, no data,
unusable quality/null, insufficient distinct ticks or sample count, then gap checks
and computation. All diagnostics remain visible even when one status takes priority.
Min/max needs one usable numeric sample; transition scans need two usable samples;
rate/trend need two distinct event ticks. Unknown unit markers cannot silently supply
a trend tolerance or limit unit; reject incompatible policy configuration. Freshness
is computed from the current sample tick even when its quality is BAD/UNCERTAIN.

### Operating limits and engineering rules

When present, a limit MUST cite the selected EngineeringContextRevision, published
rule-set ContextSourceRef, exact RuleRef `(rule_id, version)`, resolved scope binding
to the pinned plant revision, and original source/location and review provenance.
P1.3 scope resolution supplies applicability; P1.2 publication supplies reviewed
extraction/binding fidelity, not new Rule validation or execution authority.

An applicable typed comparison predicate, unit and boundary inclusion are required.
The existing Rule carrier uses descriptive strings, not an executable numeric
predicate. OD-9 therefore selects a bounded deterministic process-pack decoder for
an explicit declarative comparison grammar over an exact reviewed Rule version.
Its immutable policy fixes grammar, finite numeric parsing, units and boundary
semantics. `LimitComparison` is a rebuildable read result citing that Rule and
decoder; it never edits Rule content or creates another canonical limit registry.
Arbitrary prose, code evaluation and model interpretation are forbidden. Unsupported
grammar is explicit; failed source/scope resolution or contradictory bounds fails
closed. Decoder configuration cannot supply a bound absent from the cited Rule.
No numeric limit is extracted from free-text knowledge at read time. A limit with
unresolved conditions or incompatible units cannot drive a threshold. The
OD-3 eligibility policy permits a reviewed, canonically published reference claim for
read-only comparison while exposing its original `origin × validation × authority`
and “reference comparison” status. Compiler APPROVE alone never changes those axes
or makes the comparison an alarm, safety gate or control authorization.

No selected applicable limit is an explicit `NO_CANONICAL_LIMIT`, never a default
normal range, simulator safety margin, learned baseline, unpublished/unreviewed candidate or hidden
scenario parameter. A pinned limit that cannot resolve is a read failure, not absence.
Conflicting applicable limits fail closed; a future resolution policy must be explicit,
versioned and owner-approved. Conditional limits are unsupported until all conditions
have typed, same-snapshot evidence under an approved comparison policy.

## Serving and time consistency (OD-5)

Transport-neutral operation:

```python
get_operational_state(run_id, signal_id, *, snapshot,
                      plant_context_ref, engineering_selection,
                      interpretation_policy_refs,
                      event_start_ticks, event_end_ticks)
    -> SignalOperationalState
```

`engineering_selection` is a bounded exact list, including rule-set refs, or an
explicit empty selection; it is never “latest”. Selected refs must be available in
the READY-frozen authorized inventory. Telemetry bindings MUST agree with the selected
plant revision or an explicit immutable validated mapping. P1.1C's interim graph-source
context ref maps through its recorded ProcessGraph source; it is not relabeled in place.

P0 public queries own reader resolution and scoped interpretation inputs. Application
assembly checks run/source/clock/context/policy identity and visibility before serving
derived results. The interpreter never accesses private world state or an independent
telemetry cache. Related reads use exactly the caller's snapshot; concurrent append
does not change the result. A cache, if added, keys all exact request refs/parameters
and honors current authorization and retention.

Application API v1 §Versioning requires a new protocol version for any new method,
model or code. OD-5 selects Application API / local transport v2 with the five v1
reads retaining their meanings plus the bounded Operational State read. The existing
envelope, strict shape/range checks, request bound and redacted errors should be reused.
The host/client/native bridge/sidecar must ship matching explicit allowlists and versions;
v1 must not silently accept the new method. Wire fields and new fixed error mappings
must be finalized in the v2 owning contract before transport implementation.

Required failure families: invalid request, unknown/ambiguous binding, incompatible
clock/scope, snapshot or exact context/policy unavailable, visibility violation,
conflicting/unusable canonical limit, resource limit and internal fault. Error messages
MUST NOT echo values, hidden counts, incident ids/times, paths or exception text.
Unsupported feature kinds remain typed per-feature statuses, not fabricated values.

The application boundary remains AGENT-scope; evaluator diagnostics use a separate
trusted projection. Neither a read nor transport method steps the plant, injects a
disturbance, reviews knowledge or grants Agent authority. UI polling explicitly opens
and passes one snapshot for each refresh; it cannot imply a coherent multi-view read
when different snapshots were used.

## Live plant session (OD-4)

This is a distinct, versioned harness-owned plant-delivery lifecycle (v0).
It sits beside existing P0 runs under the
[Live plant session mode amendment](playground-backend-v0.md#live-plant-session-mode-amendment-2026-10-12).
Its states do not extend P0 RunStatus or runtime TaskStatus; existing P0 start,
RunOutcome and COMPLETED semantics remain unchanged.

Live mode is selected during preparation. Its append-capable session store attaches
to the P0 READY-frozen source inventory, immutable RunManifest and public telemetry
queries; it never reopens an existing P0 run's sealed store or uses the private test
seam in production. Session provenance pins the lifecycle version and attachment refs.
A single host owns stepping; concurrent starts, steps and recovery attempts fail.

```text
[PREPARED] -- trusted start --> [ACTIVE]
[ACTIVE] -- batch boundary pause --> [PAUSED]
[PAUSED] -- trusted resume, same incarnation --> [ACTIVE]
[ACTIVE / PAUSED] -- stop or capacity boundary --> [STOPPED]
[ACTIVE / PAUSED] -- source/ingest/ownership failure --> [FAILED]
[STOPPED / FAILED] -- new prepare + new identities --> [new PREPARED session]
```

| Operation / phase | Required observable behavior |
|---|---|
| Prepare | Attest pinned physics/config/seed, source bindings and exact context/policy refs; register the complete inventory and, for timeline-enabled sessions, the frozen timeline before READY; ingest initial sanitized observation. Failure publishes no READY manifest. |
| Start | Trusted harness acquires one stepping owner. Start is explicit; opening a read cannot start it. The plant can deliver without a Coordinator/model turn. |
| Continuous step | Reserve capacity, apply any due trusted timeline event, advance through the supported simulator interface using fixed integration/configuration, sanitize, translate once, and commit the whole source batch. Record step/event/commit association. |
| Pause / resume | Finish or resolve the in-flight batch before acknowledging pause; an unresolved batch prevents acknowledgment. No simulation/event/sequence/K progression while paused; wall time can advance. Resume retains source/clock/store identities and the committed cursor. |
| Stop | Resolve in-flight batch, release stepping owner, close physics resources, retain committed reads for the declared in-process lifetime. Never report P0 COMPLETED without the Coordinator result P0 requires. |
| Restart | Explicit new preparation/run, source incarnation, simulation clock origin/id and store incarnation/namespace. A lineage ref may cite the old session under visibility policy; no snapshot/source auto-switch. |
| Recovery | In-process retry of a known pending batch is bounded and identity-preserving. Unknown simulator/commit outcome or process loss ends that incarnation; report unavailable and prepare anew. Checkpoint/resume is deferred. |

The internal session provenance pins run/session identity, manifest checksum, simulator
and lab revisions, numerical stack, seed/config checksum, source/binding version,
clock, delivery/resource policy versions, and exact context selections. Session ids
are opaque, never derived from injection identity, timing or failed-prepare count.
Manifest and inventory remain immutable at READY. Samples, session events, failures
and outcomes are run data, not appended canonical sources. Full diagnostic provenance
is trusted-only; public projections exclude hidden configuration and ingest timestamps.
The live store contains the registered reference stream only. Counterfactual branches
retain distinct scope/identities and cannot be merged into its current value or history.

Plant stepping MUST NOT concurrently mutate an Agent-accessible reference world under
the current investigation/tool contract. OD-4 selects a harness-owned live observation
session. P1.5 owns its future pinned investigation handoff. No local frozen
copy of RcaState/world or new Agent execution path is introduced to bridge that gap.

### Clock and pacing

Simulation time supplies event ticks on the registered SIMULATION clock. Ingest time
comes from the trusted acceptance clock; K orders accepted knowledge even if wall UTC
reverses. Wall supervision/pacing uses monotonic elapsed time. None substitutes for T.
No UTC presentation of simulation time exists without an exact approved mapping.

DETERMINISTIC, ACCELERATED and REAL_TIME modes change delays only. Physics integration
steps, record cadence, values, assigned identities and event order remain fixed for
the same prepared inputs. Acceleration never enlarges physics steps or skips samples.
When behind schedule, slow delivery rather than dropping data or advancing event time
to wall time. Pause resets the pacing anchor, not simulation origin. Cross-mode evidence
compares values/event ticks/sequences with identical registered ids; trusted ingest
timestamps and wall delivery times may differ.

### Backpressure, retention and resource bounds (OD-6)

Preparation MUST pin finite bounds for pending batches, retained samples and serialized
sample bytes, commits/index metadata, simulator history/artifacts, session events and
active sessions. Count limits alone cannot bound arbitrary scalar/metadata size; maximum
record/frame sizes also apply. Reserve worst-case batch capacity before stepping.
Reserve space for the terminal outcome before admitting another step or lifecycle
event, so reaching a log/storage cap cannot prevent recording the capacity-stop.

The selected MVP uses synchronous one-batch delivery with at most one pending assigned batch;
retain every accepted record for the session and stop at the configured capacity before
the next step. This extends delivery orchestration, not append-store semantics.
A bounded session is long-lived relative to investigation turns; it need not run forever.

Under OD-6, the implementing slice proposes duration, cadence and caps sufficient for
the demonstration, including the reader's all-prefix current limit. Its PR records the
rationale and owner confirmation. No infinite loop may accumulate world history,
rollout artifacts, indices or lifecycle logs outside the declared caps.

On temporary ingestion backpressure, do not step again. Retry the exact translated
batch and identities; never retranslate a sanitized record (the translator allocates
a new sequence on each successful translation). Exhausted retries/timeouts fail the
session and retain its committed prefix. No silent drop, gap fill or reordering.

Within the declared in-process read lifetime, old snapshots remain exact while the
session runs, pauses or stops. Capacity-stop releases the producer but does not evict
data. Explicit disposal/process loss makes the old incarnation unavailable. Reads must
never fall through to a replacement session. Rolling eviction/durable segments are OD-6
alternatives requiring explicit coverage/lifetime, deduplication and storage contracts;
they are not claimed implemented or required for the MVP. No storage backend is chosen.

### Fail closed

Bad binding, unexpected sanitized fields, off-grid/nonfinite sample, identity conflict,
clock mismatch, simulator exception/shutdown, exhausted retry or lost ownership stops
further stepping. A failed batch commits none; prior committed samples remain readable
at their exact snapshots while retained. A failed step/translation cannot be retried as
if the simulator had not advanced unless the upstream interface proves that outcome.

Record the last successful step and K, pending batch ref when known, terminal reason
and exact trusted diagnostic refs. Preserve failed/negative sessions in append-only
history. Public failures are redacted and cannot serve as an incident oracle. No failure
changes a frozen manifest, canonical source, accepted sample or earlier snapshot.

## Scheduled disturbance / incident timeline (OD-7)

A frozen deterministic timeline is scheduled in LOS-B-S4, after the mandatory
clean-stream slices LOS-B-S1–LOS-B-S3. It does not gate the P1.4B exit. The owner
recorded that a clean TEP stream is nearly flat, so the “things happen over time”
demonstration needs disturbances. Neither hidden truth nor a model judgment supplies
Operational State.

For a timeline-enabled session, a deterministic versioned `DisturbanceTimeline` is
frozen before READY:
timeline id/version/checksum, exact source clock, seed/config/capability refs, and bounded
ordered entries `(entry_id, event_tick, ordinal, supported disturbance command ref)`.
TEP-native command/IDV translation remains in the process pack and uses only supported
upstream interfaces; unsupported physics is reported unsupported.

The timeline policy applies commands before integrating the step beginning at the
entry tick. It fixes same-tick ordinal ordering, valid grid, maximum entries and
handling of commands that coincide with record cadence. Reject invalid,
ambiguous or unsupported schedules before READY; never round their timing silently.
Record exact entry/step/outcome refs in trusted history. Pause delays wall execution
only; restart re-prepares the same exact schedule with new source identities. Unknown
injection outcome fails closed, with no blind retry of a possibly applied command.

Injection is trusted harness authority, never an Agent ToolSpec or application read.
Timeline identity, command identity/parameters and injection timing MUST remain
EVALUATOR-only, including manifest/inventory/log/error/artifact names and counts. Ordinary
sample event ticks remain visible; no marker identifies them as the injection time.
Operational features use the sanitized telemetry only. Derived material influenced by
timeline metadata as a computational input inherits EVALUATOR visibility; the public
summary must not consult it. Measured physical effects remain ordinary permitted
sanitized telemetry under the owning source contract. A signal change neither exposes
the injection log nor permits a feature to label its hidden cause or scheduled time.

Clean-stream sessions establish the P1.4B exit evidence first. LOS-B-S4 then adds the
repeatable incident demonstration and MUST supply LOS-B7 leakage and execution evidence.
Ad hoc operator injection needs a separate trusted command log and authority contract
and remains deferred.

## P1.3, P1.5 and benchmark interfaces

P1.3 owns PlantContextRevision, fail-closed scope resolution, the rebuildable composed
index and telemetry context re-keying (ADR-004 Decision 4). P1.4 consumes those exact
refs and does not predefine its implementation. Early telemetry-only slices can cite
the exact interim PROCESS_GRAPH source and an explicit empty engineering selection;
final P1.4A evidence exercises canonical P1.3 mapping and a real published limit.
No combined static revision or mutable “current context” is created.

P1.5 owns ContextSnapshot assembly, InvestigationContextView, Agent projection/tool
versions and the evidence provenance bridge. P1.4 exposes bounded feature/sample/policy
refs for that interface; it does not add Agent tools or promise a new investigation
start/resume contract. Future successful Agent-visible ToolResults still become immutable
ObservationRecords; Evidence still requires an explicit HypothesisEvidenceLink. Model
updates retain exact projection revision and atomic TaskStateStore.apply_batch semantics.

P1.4 changes no benchmark case, hidden candidate set, scorer, orchestration condition
or capability ladder. Frozen D0 fixtures and tests MUST remain byte-identical. Live
demo sessions cannot be substituted into D0 cases. Any later engineering context in
blind evaluation remains C6 with the required versioned leakage-audit extension; this
spec makes no capability claim. Until the deferred dirty-stream replay gate is passed,
MVP evidence labels the source as a clean deterministic simulated stream. It is not
evidence of external-plant robustness.

## Acceptance evidence

These are future observable tests/demos, not results produced by this docs-only change.
Test data uses synthetic canonical samples or authorized sanitized simulator records;
no fixture answers or evaluator truth are required for feature correctness.

### P1.4A — serving + Operational State

#### LOS-A1 — Feature correctness

Hand-computable numeric/boolean/string sequences produce each Q2 result and min/max with
exact sample/window/policy refs; endpoint trend is labeled accurately. Test increasing,
falling, stable, equal ticks, one/zero point, exact threshold boundaries, ties, gaps,
status precedence and nonfinite derived arithmetic. A window ending before T discloses
its distinct feature endpoint; transition ticks identify observations, not physical
crossing instants.

#### LOS-A2 — Quality, freshness and complete windows

BAD/UNCERTAIN/null current remains current; unusable history cannot produce a trusted
trend or bridged transition. Staleness flips exactly at the pinned tick boundary. Paused
wall time does not change the same request. Reduced history containing an omitted spike
fails rather than claiming complete min/max.

#### LOS-A3 — Canonical limits and decoder provenance

A P1.2-published canonical limit resolves through P1.3 scope and RuleRef/source
provenance. Empty selection returns NO_CANONICAL_LIMIT. Missing, conflicting,
conditional, unit-incompatible or unpublished/unreviewed limits never yield an invented
healthy/threshold state or promote Rule authority. Approved grammar decoding is
reproducible; prose/code/unsupported predicates yield no numeric bound, and changing the
decoder version changes derivation identity.

#### LOS-A4 — Time consistency

Through public P0/application reads, post-K late arrival and post-T sample change none
of value/status/count/resource outcome/provenance. Repeated reads and related-signal
results use exactly one snapshot; changed policy/context makes a distinct derivation,
never retroactive reinterpretation.

#### LOS-A5 — Selection, bounds and failures

Unknown source/signal, wrong run/incarnation/clock/context, future K, unauthorized or
missing refs and aggregate bounds fail with typed redacted errors. Peer selection is
bounded and cites exact relationships; unrelated/hidden signals affect no summary/count.
Generic tests use opaque non-TEP ids and no TEP imports.

#### LOS-A6 — Transport equivalence

Approved new transport version yields the same result as the direct service; strict
schemas, limits, fixed errors and client/bridge/sidecar allowlists agree.
Old-version/new-method requests fail explicitly. Dense provenance resolves by exact
checksum or is unavailable.

**P1.4A exit:** one bounded subsystem answers all six Q2 elements through the approved
application boundary, including explicit unavailable states and separately pinned
plant/engineering provenance. This contributes to MVP items 5 and 7; full ContextSnapshot
and Q3/Q4 integration (items 6–8) are not claimed complete by this gate.

### P1.4B — live plant mode

#### LOS-B1 — Continuous public delivery

A plant advances and commits multiple batches after preparation with no model turn/read
driving steps. Multiple explicitly opened snapshots show increasing event horizon/K and
changing telemetry; a snapshot opened during delivery resolves a whole committed batch,
never a half-batch.

#### LOS-B2 — Pacing and single ownership

Replay the same fixed config/seed/source identities/cadence under all pacing modes:
values, sequences and event order agree. Fake monotonic pacing demonstrates pause/resume
and wall-clock reversal without physics/tick changes. Duplicate start/owner attempts
fail.

#### LOS-B3 — Snapshot stability and retry identity

While appending, old T/K current/history/features remain identical. Identical
pending-batch retry advances K once; different content under the same identity rejects
atomically. No retranslation, hidden injection metadata or private-world reads appear in
public results.

#### LOS-B4 — Capacity and failure handling

Slow/failing ingestion blocks stepping at a batch boundary. Saturated
sample/byte/index/history/artifact/log caps stop before excess allocation and preserve
the committed prefix; retry exhaustion and simulator/translation/ownership failure
terminate with redacted reason. No samples are skipped to catch up.

#### LOS-B5 — Pause, stop and restart

Pause advances no simulation/sequence/K; stop leaves retained reads usable for the
declared lifetime. Restart uses new run/source/clock/store identities; old snapshots
fail after disposal/process loss and never resolve new data. Failure/negative outcomes
and trusted provenance remain in history.

#### LOS-B6 — Frozen inventory and consumer boundaries

Frozen manifest and source inventory checksums are identical before/after streaming,
pause and stop. No source is registered by a streamed sample. D0 fixture/test hashes are
unchanged; production Agent tool/projection behavior is unchanged until P1.5.

#### LOS-B7 — Scheduled timeline evidence

Required for LOS-B-S4 (OD-7): same-tick ordering, exact step boundary, pause and
cross-mode replay match; unsupported commands fail before READY, ambiguous execution
fails closed, and all injection identities/times remain absent from AGENT
payloads/refs/counts/errors. Authorized measured effects remain readable; no Operational
State feature consumes timeline metadata.

**P1.4B exit:** LOS-B1–LOS-B6 pass for the slice-proposed, owner-confirmed sustained
demonstration duration and resource envelope through TEPSimulationSource -> canonical
store -> snapshot-bound application reads. LOS-B7 is required for scheduled LOS-B-S4,
but not for the P1.4B exit. Evidence states retention/recovery limits and the clean
simulated-source qualification. This contributes to MVP item 4.

## Recommended implementation split

Each slice ships observable behavior and its evidence together; schema-only or
component-only delivery is not an independently completed slice. Dependent slices
stack after their prerequisites. No slice changes frozen benchmark surfaces.

| Slice | Vertical behavior / evidence | Dependencies |
|---|---|---|
| LOS-A-S1 | Public snapshot-bound current/freshness + numeric endpoint trend/rate/min/max with typed refs, quality handling and bounded complete windows; LOS-A1/LOS-A2/LOS-A4/LOS-A5 subset | OD-1/OD-2; approved public read extension |
| LOS-A-S2 | Canonical limit comparison and recent transitions served on the same path, including explicit no-limit/unusable cases; LOS-A1–LOS-A4 limit/transition evidence | LOS-A-S1, OD-3/OD-9, P1.2 publication + P1.3 scope mapping |
| LOS-A-S3 | Related-signal Q2 summary for the bounded subsystem with relationship refs and shared aggregate budget; LOS-A4/LOS-A5 | LOS-A-S2, P1.3 grouping/relationships |
| LOS-A-S4 | Approved versioned local transport and consumer adapters deliver the Q2 result; LOS-A6 and complete P1.4A demo | LOS-A-S3, OD-5 |
| LOS-B-S1 | Clean continuously stepped plant -> translator -> canonical commit -> public current/history; LOS-B1/LOS-B3/LOS-B6 | Playground live-mode amendment (SC-1/SC-2 resolution), OD-4/OD-6; no dependency on Q2 algorithms |
| LOS-B-S2 | Pacing plus explicit pause/resume/stop, single ownership and snapshot stability; LOS-B2/LOS-B5 lifecycle evidence | LOS-B-S1 |
| LOS-B-S3 | Capacity/backpressure/failure/restart behavior and declared read lifetime proven through public reads; LOS-B3–LOS-B6 | LOS-B-S2; completes mandatory P1.4B gate |
| LOS-B-S4 (scheduled) | Frozen timeline demonstration with trusted injection and leakage checks; LOS-B7 required | After LOS-B-S3, OD-7; does not gate P1.4B exit |

The integrated Q2-on-growing-stream evidence stacks LOS-A-S4 and LOS-B-S3. OD-8 selects
the existing local read boundary first; new UI panels belong to MVP integration.
P1.5 remains the next integration owner, not a prerequisite for a telemetry-only live
delivery proof.

## Owner decisions (2026-10-12)

The owner selected the options below on 2026-10-12; PR merge accepts this proposal.
T/K, frozen inventory, hidden injection metadata, no Agent authority and unchanged D0
remain invariants. Every selected policy/configuration is versioned and recorded.

Concrete numbers (window size, trend tolerance, gap/stale-after, demonstration duration
and capacity caps) are proposed by the implementing slice with a written rationale,
within existing reader bounds, and confirmed by the owner in that slice's PR.

| ID | Chosen option | Trade-off |
|---|---|---|
| OD-1 | Endpoint secant, strict GOOD complete-window policy, exact extrema and adjacent transitions (§Initial Q2 policies). | This is easy to audit, but mixed-quality windows suppress features and richer algorithms require new policy versions. |
| OD-2 | Reviewed process-pack policy bundle with explicit refs and disclosed resolved parameters; implementing slices propose concrete values under the rule above. | Defaults simplify callers, but cadence/unit-specific rationale is required and interpretation parameters cannot become implicit engineering limits. |
| OD-3 | Reviewed, canonically published reference comparisons after exact scope/unit/predicate validation; retain original Rule axes and reject conflicts or unsupported conditions. | This exercises P1.2 publications, but APPROVE does not establish engineering truth or execution authority. |
| OD-4 | Separate versioned harness-owned live session beside P0 runs, through the Playground live-mode amendment; attach to READY-frozen inventory/RunManifest and public reads, with investigation handoff owned by P1.5. | Distinct lifecycles preserve existing sealed P0 runs and Coordinator outcomes, but require explicit attachment and lifecycle provenance. |
| OD-5 | Direct service first, then Application API / local transport v2 in LOS-A-S4, retaining v1 read meanings; finalize wire schema and fixed errors in the v2 owning contract before adapters ship. | Staging exposes serving evidence early, but the new read requires a coordinated versioned adapter release. |
| OD-6 | Synchronous delivery with one pending batch, capped append-only in-memory retention and capacity-stop; retain reads until explicit disposal/process loss, with caps and declared read lifetime confirmed in the implementing PR. | This keeps the MVP simple, but session length is finite and durable recovery or eviction needs additional contracts. |
| OD-7 | Frozen deterministic disturbance timeline is scheduled in LOS-B-S4 after LOS-B-S3; LOS-B7 is required for that slice, neither is a P1.4B exit gate, and interactive injection stays deferred. | The nearly flat clean TEP stream needs disturbances for the “things happen over time” demo, at the cost of added execution and leakage evidence. |
| OD-8 | Existing local reads plus reproducible CLI/service demo; new UI panels belong to MVP integration. | This bounds P1.4 delivery, but final L1 still requires a visible end-to-end UI. |
| OD-9 | Bounded deterministic process-pack grammar and pinned decoder for published Rule predicate strings, producing derived LimitComparison; unsupported predicates remain unavailable. | This preserves the existing Rule carrier and ownership, but the owner must confirm grammar and publication compatibility in LOS-A-S2's PR before acceptance. |

## SPEC_CONFLICT

The descriptions below retain the original contradictions for history; the explicit
amendment resolves them for the distinct live mode.

| ID | Historical contradiction / affected behavior | Disposition |
|---|---|---|
| SC-1 | Playground backend v0 §Canonical telemetry views seals the run store before READY and has no production post-READY ingestion. ADR-005 requires continuous post-prepare delivery for live plant mode. Reusing the private post-seal test seam would violate that boundary. | Resolved by owner decision 2026-10-12 (OD-4), via [Playground §Live plant session mode (amendment 2026-10-12)](playground-backend-v0.md#live-plant-session-mode-amendment-2026-10-12): distinct live-mode ingestion; existing P0 stores remain sealed, with no production test-seam bypass. |
| SC-2 | Playground backend v0 §Run lifecycle/RunOutcome binds start to one Coordinator execution, excludes pause/cancel/checkpoint and defines COMPLETED by a valid RuntimeResult. A plant-only start/pause/stop/recovery cannot take those meanings without contradiction. | Resolved by owner decision 2026-10-12 (OD-4), via [Playground §Live plant session mode (amendment 2026-10-12)](playground-backend-v0.md#live-plant-session-mode-amendment-2026-10-12): separate versioned harness lifecycle and pinned P0 read attachment; plant-stop is not P0 COMPLETED and runtime TaskStatus is unchanged. |

No other contradiction was identified in the reviewed contracts. New transport versioning
is required evolution under Application API v1, not permission to extend v1 silently.

Implementation MUST emit SPEC_CONFLICT if it requires private-world reads, an unpinned
context/snapshot, a second assembly/state/rule owner, mutable READY inventory, hidden
truth in public features, unsupported simulator evidence, pacing-dependent physics,
production use of the test seam, or an Agent/tool/benchmark change outside its owner.
Do not invent a local bypass.
