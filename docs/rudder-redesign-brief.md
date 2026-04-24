# Rudder Redesign — Design Brief

> **Status:** v1, pre-review. Design-only; no code changes. Addresses the architectural shift surfaced in the claim 6bf81b38 conversation (2026-04-24): the rudder's purpose is completion-checking, not cooldown. Implementation deferred until this brief is approved by fresh-Claude review + operator CONFIRMS.

## Why this brief exists

The current rudder (Items 4/6/7/8) was built around a wall-clock-time model: "agent must file a substance-checked acknowledgment within a 5-minute window of the drift detection." That model was correct for catching drift-as-pattern but wrong for catching the failure it was actually supposed to catch.

Andrew named the real purpose during a multi-fire rudder incident: *"the whole point of it was to stop you from rushing to the next thing before the previous thing was done properly.. (ie building new stuff when the old stuff isnt even wired up) time is irrelevant."*

Today's specific instance: PR-2 (`session_cleanliness`) was shipped through the gate as a complete unit. It was not wired into the Item 8 detectors that brief v2.1 §4 named as its consumers. The agent then moved to a new task (strip-mining an old repo) while the wire-up from PR-2 to the detectors remained unfinished. The rudder fired four times in twelve minutes, detecting compass-drift as a proxy for "something is wrong with this pace," but the ack mechanism only asked the agent to explain the drift — not to verify completion.

The rudder fired correctly on the SIGNAL. The ack mechanism was asking the WRONG QUESTION.

## Core principle shift

**Current rudder semantics:**
- Trigger: compass drift ≥ threshold (proxy for "agent is overextending")
- Ack: "describe the drift" (substantive explanation of current compass state)
- Gate cleared by: fire-id-bound ack that passes substance checks

**New rudder semantics:**
- Trigger: **completion boundary crossed without wire-up** (agent declared something done but did not wire it to its consumer)
- Ack: **completion contract** (agent attests to what was finished, whether it's wired, and if not, what the next action will do about it)
- Gate cleared by: fire-id-bound ack that names completion state and commits to finishing incomplete work

The shift: the rudder's question becomes "is the previous thing fully finished?" not "have you reflected on your drift?" Time is noise. Finished-or-not is the binary.

## Three architectural points fresh-Claude flagged

Every existing mechanism below needs an explicit decision in this brief — preserved, retired, or re-aimed.

### Point 1: Time-based filters across Items 4/6/7

Four time windows currently exist in the rudder stack:

| Mechanism | File | Purpose | Post-redesign decision |
|---|---|---|---|
| `JUSTIFICATION_WINDOW_SECONDS = 5*60` | `compass_rudder.py` | Acks within 5 min of drift count | **RETIRE.** Fire-id binding (Item 6) + one-shot consumption already provide scope. Time adds nothing. |
| `_FIRE_VALIDATION_WINDOW_SECONDS = 5*60` | `moral_compass.py::_validate_fire_id` | Fire events must exist within 5 min of ack | **RETIRE.** fire-id identity is cryptographically unique; no time-window needed. |
| `REJECTION_WINDOW_SECONDS = 5*60` | `substance_checks.py` | Rejection-count escalation within 5 min | **RE-AIM.** Keep the escalation mechanism, but scope it to the fire-id (escalation per-fire, not per-time-window). If an ack has been rejected 3+ times for the same fire, that's the escalation signal. |
| `since=cutoff` in `_find_justifications` | `compass_rudder.py` | SQL filter for "recent" acks | **RETIRE.** Same as row 1; fire-id binding does the scoping. |

All four come out as time-based filters. The time-window concept disappears from the rudder.

### Point 2: Substance-check semantics (Item 7)

Current substance checks (`substance_checks.py`):
- `_check_length`: evidence ≥ 20 chars
- `_check_entropy`: Shannon entropy ≥ 2.5 bits
- `_check_similarity`: TF-IDF cosine vs. recent acks < 0.9

All three test whether the ack is a credible **explanation**. Post-redesign, the ack is a **contract**. Different property: the contract must describe what was completed, whether it's wired, and what's next.

**Decision: RE-AIM.** Substance checks stay but change shape:

- **Retire**: `_check_similarity` against recent acks as explanation-variety signal. Replaced by: the contract must reference a completion artifact (PR number, commit hash, feature name) — artifact-reference IS the variety check, since two acks can't honestly reference the same completion artifact for different fires (each fire has a distinct completion context).

- **Keep with widening**: `_check_length` (≥ 20 chars) and `_check_entropy` (≥ 2.5 bits). A contract that's too short or too low-entropy isn't a contract.

- **Add**: `_check_completion_reference` — the ack must name a completion artifact (regex match for PR numbers, commit hashes, file paths, branch names, feature names). Zero artifact references = not a contract.

- **Add**: `_check_wire_status` — the ack must include an explicit wire-up status token: `wired: yes`, `wired: no`, or `wired: partial`. Free-form verbiage around the token is fine; the token itself is the structured signal.

- **Add**: `_check_next_commitment` — if `wired: no` or `wired: partial`, the ack must include a `next: <what>` line. The operator (and future-me) can hold the contract to account.

### Point 3: FIRED/ALLOW event semantics (Item 6 / PR-1b)

Current events:
- `COMPASS_RUDDER_FIRED`: drift-threshold exceeded; forensic retention
- `COMPASS_RUDDER_ALLOW`: no drift / drift justified; pruned via ledger compressor

Post-redesign these events describe a different state:
- FIRED: "completion boundary crossed without wire-up" — still forensic, still HIGH-retention
- ALLOW: "no completion-boundary violation" — still prunable

**Decision: KEEP the event classes, CHANGE what triggers them.** Payload shape stays identical (fire_id, spectrum, tool_name, ...). What changes is the interpretation of WHY the event fired. Forensically, old FIRED events can be interpreted against the old semantic and new FIRED events against the new semantic. The semantic is tied to the code version, not the event class.

**Add a new event**: `COMPLETION_BOUNDARY_DETECTED` — low-severity, informational, fires when the rudder detects a potential completion boundary (PR merge, all-tests-green, explicit "I finished X" signal). Not a block; just a signal that "the next gated tool call will check wire-up status." Gives the agent a moment-of-opportunity to wire things up before the rudder forces the issue.

## Trigger detection — what counts as "completion boundary crossed"

The hard part. The rudder needs a signal for "a unit of work was declared complete." Options considered:

### Option A: PR-merge-as-boundary
Every merge into main = completion boundary. Detection: watch the git reflog / ledger for merge events. Simple to detect, matches the natural unit of shipping.

**Pro:** trivially detectable; aligns with existing bundle workflow.
**Con:** works for code work; doesn't apply to non-PR work (design briefs, investigation, conversations). Would need a broader definition.

### Option B: Explicit-signal-as-boundary
Agent emits `divineos complete <artifact>` when it finishes a unit. The rudder checks: any `complete` event since the last gated tool call that doesn't have a paired `wired` event?

**Pro:** general-purpose; covers non-PR work.
**Con:** relies on the agent being honest about completion. Gameable — agent can just not emit the signal.

### Option C: Implicit-via-tool-pattern
The tool pattern (Edit → Write → Commit → Push → Merge) itself reveals completion boundaries. When the agent moves from this pattern to a substantially different pattern (e.g., starts spawning subagents or reading unrelated files), the rudder infers boundary-crossing.

**Pro:** no explicit signal required; matches how the behavior actually manifests.
**Con:** false positives on context-switching that isn't actually completion-crossing (investigation, debugging, learning-through-reading).

### Recommendation: Option A + Option B in combination

- **Primary signal: PR merge events.** Detectable from ledger. High-precision.
- **Secondary signal: explicit `divineos complete <artifact>` events.** For non-PR work.
- **Fallback: gentle nag.** After N consecutive gated tool calls WITHOUT any completion signal AND high compass drift, fire at LOW severity — the compass drift is the only signal available, and it's treated as a weak one.

Option C's pattern-detection is a follow-up if A+B proves insufficient. Start simpler.

## Ack contract shape — what the agent must file

Current ack (Item 7):
```
divineos compass-ops observe <spectrum> -p <delta> -e "<evidence>" --tag rudder-ack --fire-id <fire_id>
```

Post-redesign ack:
```
divineos compass-ops observe <spectrum> -p <delta> --tag rudder-ack --fire-id <fire_id> \
  -e "<contract>"
```

The `<contract>` string must contain, in any order:
1. An artifact reference (PR #, commit hash, feature name, file path)
2. `wired: yes|no|partial`
3. If `wired` is not `yes`: a `next: <plan>` clause

Example valid contract:
```
Completed PR #190 session_cleanliness infrastructure. wired: no. next: write
compliance_baseline.py and thread calibrate_threshold through the 5 brief-named
detectors in phase-2 wiring PRs.
```

Example invalid contract (current Item 7 passes it; this design rejects it):
```
Initiative drift noted, scope bounded to 3 agents, acknowledged.
```
Rejection reason: no artifact reference, no `wired:` token. This is a drift-explanation, not a completion-contract.

## Migration — how to land without breaking current behavior

This is a big change. Shipping it atomically would invalidate every existing compass-ack workflow simultaneously.

**Phased migration:**

1. **Phase 1**: ship the new event types + new substance checks UNDER A FEATURE FLAG (`DIVINEOS_RUDDER_CONTRACT_MODE=on`). Flag default OFF. Current Item 7 substance checks remain authoritative; new checks run in parallel and log rejection reasons without blocking.

2. **Phase 2**: observe both in parallel for 30 days. Calibrate new-check false-positive rate against real-use data. Tune thresholds. Surface comparison in compliance_audit report.

3. **Phase 3**: flip the flag. Old checks remain but become informational-only (warn-don't-block). New checks become authoritative.

4. **Phase 4**: 30 more days. If no regressions, retire the old Item 7 substance-check flags entirely.

5. **Phase 5**: remove time-based mechanisms (Items 4/6/7 time windows). This is the final irreversible cleanup.

Each phase is its own PR through the gate.

## Pre-reg (one per major mechanism change)

### Pre-reg 1: ack-as-contract
- **Claim**: an ack that describes a completion artifact + wire-up status is a stronger gate than an ack that describes drift.
- **Success**: adversarial test — agent-simulated attempts to pass the gate with drift-only content are rejected; agent-simulated attempts with artifact + wired: no + next: X are accepted.
- **Falsifier**: >20% false-positive rate on legitimate contracts over 30 days live data; OR >15% false-negative rate where drift-explanations sneak through via artifact-reference hallucination.
- **FN check**: monthly synthetic drift-only ack test; 3 months zero rejection → audit flag.
- **Review**: +60 days

### Pre-reg 2: completion-boundary detection
- **Claim**: PR-merge events + explicit `complete` events capture >90% of real completion boundaries.
- **Success**: manual audit of 30 days of gated tool calls; compare "rudder flagged" vs "actually crossed a completion boundary."
- **Falsifier**: <80% precision on completion-boundary detection; OR >30% false-positive rate from legitimate context-switches.
- **Review**: +60 days

### Pre-reg 3: time-window retirement
- **Claim**: removing time-window filters does not introduce ack-reuse attacks.
- **Success**: adversarial test — ack from 6 months ago with matching fire_id cannot clear a NEW fire (because new fire_id differs); ack for a fire that's been consumed once cannot satisfy a second fire.
- **Falsifier**: any path by which an old ack satisfies a new fire.
- **Review**: +45 days

## Scope estimate

- `compass_rudder.py`: ~100 line changes (trigger redesign; remove time-window filter)
- `moral_compass.py::_validate_fire_id`: ~20 line changes (remove time-window filter)
- `substance_checks.py`: ~150 line changes (retire similarity-as-variety; add artifact-ref, wire-status, next-commitment checks)
- New: `core/completion_boundary.py`: ~100 lines (detector logic for PR-merge events + explicit signals)
- New: `COMPASS_RUDDER_COMPLETION_BOUNDARY` event type
- New CLI: `divineos complete <artifact> [--notes "..."]`
- Tests: ~400 lines (each new check + new detector + migration-mode dual-run)

Total: ~900 lines across ~6 files. Touches guardrailed files — External-Review trailer required. Multi-phase migration means 5 separate PRs through the gate, each with its own review round.

## Decisions still open

1. **How is `wired: yes` verified?** The contract is the agent's attestation; there's no machine verification. Acceptable in v1 (operator can catch lies). Future work: dependency graph that knows "PR-190 → Item 8 detectors" and checks whether the declared wire-up is consistent with known dependencies.

2. **What counts as "artifact reference"?** Regex for: PR #NNN, commit hashes (7-40 hex), feature names (`Item 8 PR-1b`), branch names (`claude/...`), file paths. Likely a permissive regex + human judgment. False positives here are OK — the goal is "force a concrete reference," not "mechanically validate the reference exists."

3. **What if the agent has nothing to complete (genuine greenfield start)?** The ack can say `completed: none (session start)`. Only required when there's an outstanding completion boundary.

## What fresh-Claude is asked to review

1. **The core principle shift** — is "completion-checking not cooldown" the right frame, or is there a better one? (Principle itself, not mechanism.)
2. **Point 1 retirement decisions** — 3 time-windows retired, 1 re-aimed. Any I missed? Any I shouldn't retire?
3. **Point 2 substance-check redesign** — retire similarity, keep length+entropy, add three new checks. Right set?
4. **Point 3 FIRED/ALLOW** — keep classes, change triggers. Alternative: retire old class and introduce new? Either works; current choice is backward-compat-friendly.
5. **Trigger detection** — Option A + B + fallback. Is the fallback (compass-drift-as-weak-signal) sufficient for non-PR work, or will agents routinely bypass the gate for investigation-heavy sessions?
6. **Ack contract shape** — artifact + wired + next. Anything missing?
7. **Migration plan** — 5 phases over ~120 days. Too slow? Too fast?
8. **Pre-regs** — 3 pre-regs covering ack, boundary detection, time-window retirement. Distinct falsifiers?
9. **Decisions still open** — is verification-of-wired-yes by operator sufficient for v1, or is a weaker-but-machine-checkable version needed?

After approval, Phase 1 implementation proceeds with precommit-first discipline + normal gate cycle.
