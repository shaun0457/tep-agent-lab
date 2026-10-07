# Industrial Context Platform — MVP and Product Maturity

Status: product scope guardrail; complements `industrial-context-platform-north-star.md`  
Owner repo: `tep-agent-lab`  
Purpose: distinguish research infrastructure, portfolio MVP, pilot-grade product, production product and long-term platform scope.

## Why this exists

The architecture is intentionally broader than the first implementation slice.

Do not confuse:

```text
architecture completeness
with
implementation breadth
```

The long-term architecture may include many industrial sources, context compilation, context serving, Agent investigation and permissioned actions.

The MVP proves one deep, credible vertical slice.

Adding more connectors, more documents or more UI does not automatically advance product maturity unless the corresponding acceptance criteria are met.

## Product question hierarchy

The product should progressively answer four questions.

### Q1 — What values exist now?

```text
current signal values
quality
freshness
source
```

Necessary but not sufficient.

### Q2 — What is changing now?

```text
trend
rate of change
threshold state
recent transition
staleness
related-signal changes
```

This is operational state interpretation.

### Q3 — What does that mean in plant context?

```text
affected asset/subsystem
relevant topology
operating limits
engineering rules
SOP/manual/control context
known data-quality limitations
```

This is contextualized operational awareness.

### Q4 — Why might it be happening and what should be investigated?

```text
observations
competing hypotheses
evidence
experiments / counterfactuals
diagnosis
remaining uncertainty
```

This is the investigation layer.

The portfolio MVP must reach Q4 for one bounded subsystem.

## Level 0 — Research Foundation

Purpose:

> prove deterministic Agent/runtime/evaluation mechanics.

Expected capabilities:

- deterministic environment;
- typed tool surface;
- ProcessGraph/topology;
- observations and evidence separation;
- bounded experiments/counterfactuals;
- trace/provenance;
- benchmark fixtures;
- hidden evaluator truth;
- deterministic scoring/audit.

Current TEP D0 work primarily belongs here.

This level is necessary infrastructure, but by itself is not the portfolio MVP.

A system at Level 0 may still be only a research harness.

## Level 1 — Portfolio MVP

Name:

> **Industrial Context Platform — Investigation-first MVP**

Scope:

> one bounded industrial subsystem, with one deep end-to-end flow.

The initial implementation may use TEP as its physics/telemetry source, but the demonstrated product concepts must be source-independent above the adapter boundary.

### Required end-to-end flow

```text
Engineering Sources
SOP / manual / limits / control description
        |
        v
Context Compiler
typed extraction
semantic grounding
Candidate Context
        |
        v
Human / deterministic verification
        |
        v
Canonical Context Revision
        |
        +-----------------------+
        |                       |
        v                       v
Plant semantics            Live telemetry
topology / signals         TimeSeriesStore
        |                       |
        +-----------+-----------+
                    |
                    v
              Context Server
                    |
                    v
             ContextSnapshot
                    |
                    v
          Operational awareness
          "what is happening now?"
                    |
                    v
          Investigation Runtime
 Observation -> Hypothesis -> Evidence
 Experiment -> Diagnosis
                    |
                    v
                   UI
       "why did the Agent know this?"
```

### Required MVP capabilities

#### A. Context Compiler

A small but real engineering corpus is ingested.

Recommended initial corpus:

- subsystem operating description;
- troubleshooting SOP;
- control-loop description;
- operating limits;
- one alarm/response or maintenance guidance document.

The compiler must demonstrate:

- typed extraction;
- semantic grounding to entities/signals;
- Candidate Context;
- ambiguous/low-confidence binding review;
- approve/reject/remap;
- immutable Canonical Context revision;
- exact source provenance.

LLM/VLM output cannot become canonical truth directly.

#### B. Plant semantics

The subsystem has:

- canonical entities;
- signal descriptors;
- topology/relationships;
- source/version provenance.

TEP-specific IDs may exist inside the adapter/lab, but generic context contracts must not require them.

#### C. Live operational state

Telemetry is delivered continuously through the canonical telemetry path.

At minimum the system can derive deterministic state features such as:

- current value;
- quality;
- freshness;
- recent trend;
- rate of change;
- min/max over bounded window;
- threshold/limit state;
- recent transition.

This may be implemented as a small deterministic **State Interpreter** or equivalent derived-state component.

It is not a full anomaly-detection platform.

#### D. ContextSnapshot

At investigation time T, the system pins:

- plant context revision;
- telemetry event horizon T;
- ingestion cutoff K;
- exact engineering knowledge/document/rule revisions;
- visibility/projection policy;
- assembly provenance.

Historical replay must not silently substitute today's latest context.

#### E. "What is happening now?"

For the bounded subsystem, the product can produce a structured operational summary without evaluator truth.

Example semantic output:

```text
Subsystem:
  reactor cooling

Current condition:
  thermal deterioration / attention

Observed changes:
  reactor temperature rising
  cooling outlet temperature rising with lag
  valve command near recent baseline

Relevant engineering context:
  high-temperature operating limit
  cooling troubleshooting SOP
  temperature-control description
```

The exact wording may be model-generated, but the underlying signals, trends, rules and refs must be typed and traceable.

#### F. Investigation

A real model performs an evidence-backed investigation using bounded context/tools.

The system demonstrates:

- Observation;
- competing Hypotheses;
- explicit EvidenceLinks;
- relevant engineering-knowledge refs;
- optional deterministic analysis/counterfactual experiment;
- structured diagnosis;
- remaining uncertainty;
- resource/trace provenance.

#### G. Explainability / portfolio UI

The UI must make the architecture visible.

At minimum it shows:

- plant/subsystem topology;
- live time series;
- current operational state;
- relevant engineering context;
- investigation trace;
- evidence/source refs;
- context revision/snapshot;
- "why did the Agent know this?"

A chat-only interface is not sufficient for this MVP.

### MVP non-goals

The Portfolio MVP does **not** require:

- whole-factory automatic ontology;
- arbitrary engineering-document support across every industry;
- production OPC UA security;
- MES/ERP/CMMS integration;
- full alarm-management platform;
- enterprise RBAC/SSO;
- high availability;
- autonomous plant control;
- automatic promotion of Agent conclusions into trusted knowledge;
- multiple live customer plants;
- final industrial product scalability.

### Portfolio MVP exit criterion

Level 1 is achieved only when one visible demo proves:

1. source material becomes typed Candidate Context;
2. at least one ambiguous mapping requires human verification;
3. approval publishes a new canonical context revision;
4. live telemetry enters through canonical SignalSample/store/reader contracts;
5. deterministic operational-state interpretation answers Q2;
6. plant + telemetry + engineering knowledge assemble into a time-consistent ContextSnapshot;
7. the system answers Q3: "what is happening now in this subsystem?";
8. a real Agent performs evidence-backed investigation for Q4;
9. every important claim can be traced to exact source/snapshot refs;
10. TEP-specific semantics remain below the generic context boundary.

## Level 2 — Pilot-grade Product

Purpose:

> prove the architecture can leave the TEP-only world and survive a realistic deployment boundary.

Required additions beyond Level 1:

- at least one non-TEP source/domain;
- second-source abstraction review under the two-domain rule;
- external historian replay or OPC UA read-only connector;
- durable context revisions;
- incremental source re-ingestion / semantic diff;
- review queue for candidate bindings/claims;
- context migration/version handling;
- persistent telemetry/history suitable for replay;
- explicit site/source configuration;
- operational monitoring and ingestion diagnostics;
- basic user/access boundary;
- repeatable deployment;
- offline/replay investigation on captured real or realistic data.

Recommended proof:

```text
TEP source
+
one external/non-TEP source
        |
        v
same canonical context / telemetry contracts
        |
        v
same Context Server
        |
        v
same Investigation Runtime
```

Level 2 does not require autonomous writes to plant systems.

## Level 3 — Production-grade Product

Purpose:

> operate safely and reliably in real industrial environments.

Typical required capabilities:

- production authentication / authorization;
- tenant/site isolation where relevant;
- secure secret/credential handling;
- production OPC UA/historian connector hardening;
- connector lifecycle/reconnect/backpressure behavior;
- durable storage and migration strategy;
- backup/recovery;
- audit logs;
- observability;
- data-retention policy;
- access policy for engineering documents and telemetry;
- deployment/upgrade strategy;
- failure isolation;
- performance/resource limits;
- security review;
- documented SLO/SLA targets where appropriate;
- model/provider governance;
- human approval for operational recommendations/actions.

If permissioned writes/actions are introduced, they require a separate safety/authority design.

A read-only investigation product can be production-grade without autonomous control.

## Level 4 — Industrial Context Platform

Purpose:

> broaden from one investigation vertical into reusable industrial context infrastructure.

Potential capabilities:

- multiple plants/sites;
- multiple industrial domains;
- MES / ERP / CMMS / QMS adapters;
- work orders / maintenance / operator decisions;
- reusable ontology/schema extensions;
- broader Context Compiler source coverage;
- richer operational context graph;
- multiple Agent use cases;
- decision/outcome write-back as validated candidate knowledge;
- planning / quality / maintenance / compliance workflows;
- permissioned actions under explicit authority and safety policy.

This is the long-term platform level.

It is not the MVP gate.

## Product maturity summary

| Level | Name | Core proof |
|---|---|---|
| L0 | Research Foundation | deterministic Agent/runtime/evaluation works |
| L1 | Portfolio MVP | one subsystem: context compilation + live state + ContextSnapshot + real investigation |
| L2 | Pilot-grade Product | same architecture works with a non-TEP source and durable/reviewable operation |
| L3 | Production-grade Product | secure, reliable, observable, governed real deployment |
| L4 | Industrial Context Platform | multiple domains/sources/use cases on reusable context infrastructure |

## MVP implementation priority

For Level 1, prioritize depth in this order:

```text
P1.0
architecture / ownership / snapshot semantics

P1.1
live telemetry foundation

P1.2
engineering-knowledge contextualization
Candidate -> Review -> Canonical

P1.3
canonical context composition / graph bindings

P1.4
context serving + deterministic operational state
"What is happening now?"

P1.5
ContextSnapshot + Investigation Context integration

D1 / investigation capability
real model + evidence-backed RCA

MVP integration
portfolio UI and end-to-end demonstration
```

D0 benchmark work remains a research/evaluation track and must not expand indefinitely before the Level-1 vertical slice is integrated.

## Scope-change rule

Before adding a major new connector, datastore, UI framework, ontology layer or Agent architecture, ask:

> Does this unblock the next unmet acceptance criterion of the current maturity level?

If not, defer it unless there is a documented architectural reason.

This rule exists to prevent a full-platform vision from turning the MVP into an unbounded enterprise build.
