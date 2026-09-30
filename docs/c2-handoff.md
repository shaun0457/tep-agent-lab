# C2 independent rule registry handoff

- Branch: `feat/rule-registry-v0`.
- Owner: `docs/specs/knowledge-rule-registry-v0.md` (acceptance 1–7).
- Files: `src/tep_agent_lab/rules.py`, `tests/test_rules.py`, this handoff.
- Dependencies: Python 3.13 stdlib only. Packaging is coordinator-owned.

## Implemented

Typed independent origin, validation and authority axes; frozen rule/source/version
records and registry snapshots; exact provenance and extraction metadata; explicit
conflicts retaining pinned rules and their metadata; deterministic selection of
configured hard-gate versions in an exact scope. Rules and predicates are data,
not executable evaluation code. Opaque versions have no inferred latest ordering.

Host configuration constructs registries. Agent metadata-update requests are
rejected; literature extraction can only construct a NONE/REFERENCE candidate.
Hard-gate origin/review checks prevent evidence maturity from becoming execution
authority. Existing versions cannot be overwritten. Historical pins remain usable
after a new version narrows or demotes the current configuration.

The concrete blind-RCA policy references accepted ADR-001 at its exact source
commit. Other representative relations and intervention limits are explicitly
test fixtures. No simulator physics or numeric operating constraint is invented.
`SourceVersion` is a bibliographic citation, not a fork of runtime InformationRef.

## Verification

Acceptance and full current branch regression:

```powershell
$env:PYTHONPATH='src'
py -3.13 -m unittest discover -s tests -v
```

Output: `Ran 12 tests ... OK`.

`git diff --check` passes. Boundary inspection: imports are stdlib only; no runtime
contracts, simulator physics, C1/C3 state, tool registration, or promotion engine
are implemented here.

## Remaining integration

Real A1/B1 consumer request validation must pin configured rules and map its typed
request checks to these metadata records. Fake-consumer acceptance verifies
authority selection only; it is not production gate execution. Agent tool wiring,
automatic conflict detection, promotion, and general scope/envelope reasoning are
out of scope. Status remains descriptive metadata; the host explicitly chooses
active version refs instead of inferring lifecycle semantics from free text.

SPEC_CONFLICT: none. Commit SHA is supplied in the coordinator handoff (avoids a
self-referential commit hash in this file).
