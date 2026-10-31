# Proposal materials: verification slice

**From:** Member 4 (verification)
**For:** proposal owner (member 1) and the team
**Date:** 2026-10-20
**Purpose:** ready-to-use material for the proposal, with every claim labelled implemented / planned / blocked.

> **Rule:** this document distinguishes what was *run* from what is *planned*. Do not promote a
> planned item to implemented in the proposal without a recorded command and result.

---

## 1. One-paragraph summary (safe to quote)

The verification slice decides whether a repair candidate may be called successful. It is a
pure, deterministic function: evidence (obligations) goes in, one of six outcome labels comes
out. It performs no I/O, never executes a binary, and is the only place in the system that
computes a success label — the GUI and the API render the verdict, they never derive it. The
engine is backed by 29 negative-first unit tests; the identity/state contract is verified
against the job controller; and a behavioural oracle for the selected CWE-121 fixture is frozen
so both repair routes are asserted against one standard.

## 2. Acceptance semantics (implemented)

Six terminal outcomes, five check results.

| Outcome | Meaning | Can satisfy MVP |
|---|---|---|
| `success_within_scope` | every required obligation passed, hashes match, scope reviewed | yes |
| `unsupported` | capability outside the supported profile | no |
| `rejected` | a candidate failed a required check | no |
| `inconclusive` | evidence cannot establish a required property | no |
| `error` | tool, provider, or system failed | no |
| `cancelled` | operator stopped the attempt | no |

Decision precedence (first match wins): cancellation > error > unsupported > required failure >
required `not_run`/`unknown`/`timed_out` > hash mismatch > empty scope > no required evidence >
success. Two rules matter most:

- a definite failure outranks missing evidence (a shown-dangerous candidate is `rejected`, not
  softened to `inconclusive`);
- an empty obligation list is never success (checks cannot be silently dropped).

Run-level: both routes must be `success_within_scope`; one successful route is not a successful
MVP run.

## 3. Verification ladder status (honest)

| Gate | What it establishes | Status |
|---|---|---|
| V0 identity / preconditions | hashes, approval, required inputs | **implemented** — acceptance rules + job controller; identity contract verified 8/8 |
| V1 structure / placement | candidate meets the supported structural profile | **planned** — fixture candidate built (ELF64 `ET_EXEC`, non-PIE, symbols); checks not written |
| V2 interface / compatibility | ABI/runtime compatibility evidence | **planned** |
| V3 concrete behaviour | valid suite, invalid oracle, regression | **planned** — manifest and oracle frozen; assertions not written |
| V4 bounded symbolic security | the required property holds within the reported domain | **blocked** — guest has no angr/Claripy/Z3 |
| V5 reproducibility | hashes, versions, replay | **partial** — versions and lock recorded; image hash pending |

## 4. Test evidence (actually run)

| Suite | Count | Where | Status |
|---|---|---|---|
| Acceptance rules (`tests/unit/test_acceptance.py`) | 29 | member-4 branch | **passing** |
| AI boundary contract | 22 | member-5 branch (`ea29e56`) | reported passing by member 5 |
| Identity/state contract | 8 | against `member1/jobs-minimal` `f41558a` | **8/8 verified** (equivalent script; the sandbox blocks pytest `tmp_path`) |
| Full branch suite | 40 passed, 1 skipped | member-4 branch | **passing** |

The 1 skip is the identity contract skeleton on the branch (it activates once the job
controller is present in the merged tree). A skip is not a pass.

## 5. What the proposal can claim, and must not

**Can claim (with evidence):**

- a deterministic acceptance engine with a fixed six-outcome vocabulary and documented
  precedence, backed by 29 negative-first tests;
- an identity/state contract verified against a real job controller (8/8);
- a frozen behavioural oracle for the public CWE-121 fixture (capacity, off-by-one rule, exact
  error bytes, and the requirement that both routes emit identical bytes);
- the AI candidate layer's local validation and typed provider failures (member 5, 22 tests).

**Must not claim:**

- V4 symbolic verification (toolchain missing);
- Route B proven feasible on a real ELF (only synthetic Mach-O flow tests so far);
- a real DeepSeek call (all tests use a fake transport; no API key, no verified retention);
- any repaired or verified binary — none exists yet.

## 6. Risks the proposal should state

1. **V4 toolchain missing** blocks F4 acceptance until angr/Claripy/Z3/LIEF/clang/Ghidra are
   installed and smoke-tested (decision requested separately).
2. **Route B feasibility unproven on the real fixture** — this is the largest schedule risk;
   both routes are P0 and one failure blocks MVP acceptance.
3. **Guest isolation incomplete** (passwordless sudo, NAT, no snapshot) — candidate execution
   is not yet safe and any "isolated" claim would be false.
4. **Public Juliet fixture** means a model may recognise the intended fix; results cannot
   support a claim of independent binary analysis.
5. **No real provider call yet** — cost, retention, and model version remain unverified.

## 7. Suggested proposal subsection (copy into the proposal)

> **Verification approach.** Acceptance is computed by a single deterministic engine that maps
> evidence obligations to one of six outcomes; the UI renders outcomes and never derives them.
> Each candidate is checked against a frozen contract through the ladder V0–V5: identity and
> approval, structure and placement, interface compatibility, concrete behaviour, bounded
> symbolic security, and reproducibility. A missing, unknown, or timed-out required check
> yields `inconclusive`, never success. At the time of this proposal the engine, the identity
> contract, and the fixture oracle are implemented and tested; structural checks, behavioural
> assertions, and symbolic verification are specified but not yet implemented, and are listed
> as the next milestones rather than reported as complete.

---

## 8. Open items to resolve before the proposal is submitted

1. V4 toolchain decision (install vs `unsupported`) — see `07-v4-isolation-decision.md`.
2. Fixture freeze confirmation after gates 3–5.
3. Route B feasibility result on the real ELF.
4. Provider key and retention verification.
