# Item 8 PR-1b Design Addendum

> **Status:** v1, pre-review. Small focused addendum to `docs/item-8-detector-extensions-revision.md` (v2.1) covering two open design questions raised in fresh-Claude's PR-1a round-2 review that need settling before PR-1b implementation. Implementation deferred until this addendum is approved.

## Context

PR-1a shipped 5 detectors + feature flags. Deferred to PR-1b:

1. `rudder_infrastructure_failure` detector + event emission (needs real active-session source, not fires+allows proxy)
2. HIGH-severity ledger event emission (brief §5 response-path) — `COMPLIANCE_DRIFT_HIGH`, `RUDDER_INFRASTRUCTURE_FAILURE`
3. Variance-collapse, content-entropy, multi-window meta detectors
4. Cross-detector aggregation in `format_report` (brief §5.5)

The three detectors in (3) are already spec'd in v2.1 §2 and §2c. (2) is straightforward — emit `log_event(event_type=..., payload=...)` at the appropriate severity branch. (1) and (4) have design questions that need a short decision before code.

This addendum settles (1) and (4). Everything else follows the v2.1 spec.

---

## Q1. Active-session source — `COMPASS_RUDDER_INVOKED` counter

### Problem

The brief v2.1 §3 specs `rudder_infrastructure_failure` as firing when FIRED==0 AND ALLOW==0 in an "active session." PR-1a used fires+allows as the active-session proxy. Fresh-Claude round-2 caught the contradiction: the branch is unreachable — if both are zero the "active" condition can't hold.

We need a signal that says "the rudder ran (or was supposed to run), but produced no events." Three candidate sources:

**Option A: `COMPASS_RUDDER_INVOKED` event at the start of `check_tool_use`.**

```python
def check_tool_use(...):
    _emit_invoked_event(tool_name)   # NEW
    if tool_name not in GATED_TOOL_NAMES:
        return RudderVerdict(decision="allow", ...)
    # ... existing logic
```

Every call emits INVOKED. If the rudder is broken *later* in the pipeline (hook fails to call check_tool_use at all), no INVOKED event fires — and that's the signal we actually want to detect.

Wait — that contradicts itself. If the hook fails to call check_tool_use, no INVOKED fires either. This option only catches "check_tool_use called but internal failure prevented allow/fire emission." Narrower than "gate dead."

**Option B: Count gated-tool calls from an external source (hook invocations from a shell-level log).**

Every PreToolUse hook invocation gets logged by the hook infrastructure regardless of whether the Python process runs. Compare hook-invocation count to COMPASS_RUDDER_ALLOW+FIRED count. If hook-count > 0 AND rudder-event-count == 0, the Python process isn't emitting events — infrastructure failure confirmed.

Cleaner because the signal source is outside the failing component. Matches the "independent verification" principle — we don't trust the broken thing to say it's broken.

**Option C: Use `TOOL_CALL` ledger events (already emitted by `core/tool_wrapper.py`).**

The tool wrapper emits `TOOL_CALL` events at invocation time. Filter to gated tool names (Task, Agent). Count gives us "gated tools actually ran." Compare to FIRED+ALLOW count for the same window. Mismatch = rudder not catching them.

No new code path needed. Just a read against an existing ledger stream.

### Decision

**Option C — use `TOOL_CALL` events as the active-session source.** Rationale:

- Already-emitted data stream; no new instrumentation required
- Independent of the component being tested (rudder emission path)
- Cheap read against existing ledger
- Cleanly distinguishes three cases:
    - TOOL_CALL (gated) == 0 → no activity, quiet session, no anomaly
    - TOOL_CALL (gated) > 0 AND FIRED+ALLOW == TOOL_CALL (gated) → healthy
    - TOOL_CALL (gated) > 0 AND FIRED+ALLOW < TOOL_CALL (gated) → infrastructure-failure shape

Option A requires new emission code inside the component-under-test (fragile — the failure mode it catches is exactly the failure mode that could suppress the emission itself). Option B requires cross-process correlation which is heavier than needed for v1.

### Implementation

In `_detect_block_allow_anomalies`:

```python
tool_calls = [
    e for e in get_events(event_type="TOOL_CALL", limit=5000)
    if e.get("timestamp", 0.0) >= cutoff
    and (e.get("payload") or {}).get("tool_name") in {"Task", "Agent"}
]
gated_activity = len(tool_calls)

if gated_activity < _BLOCK_ALLOW_ACTIVE_SESSION_GATED_CALLS:
    return anomalies  # quiet session

expected_events = gated_activity  # each gated TOOL_CALL should produce exactly one ALLOW or FIRE
actual_events = len(fires) + len(allows)

if actual_events == 0:
    # Pure infrastructure failure — the rudder wasn't invoked at all
    anomalies.append(
        Anomaly(
            name="rudder_infrastructure_failure",
            severity=AnomalySeverity.HIGH,
            observation=f"{gated_activity} gated tool calls ran but 0 rudder events emitted. Gate-dead.",
            ...
        )
    )
    return anomalies

if actual_events < expected_events * 0.8:
    # Partial infrastructure failure — some calls bypass the rudder
    anomalies.append(
        Anomaly(
            name="rudder_partial_infrastructure_failure",
            severity=AnomalySeverity.MEDIUM,
            observation=f"rudder emitted {actual_events} events for {gated_activity} gated calls ({actual_events/gated_activity:.0%}).",
            ...
        )
    )
```

Adds a bonus `rudder_partial_infrastructure_failure` MEDIUM anomaly for the case where SOME calls are going through but not all — catches subtler hook-registration issues.

### Pre-reg update for 7.3a

Adversarial scenarios extended:

- 100 TOOL_CALL(Task) with 100 allows → no anomaly
- 100 TOOL_CALL(Task) with 0 rudder events → rudder_infrastructure_failure HIGH
- 100 TOOL_CALL(Task) with 50 allows, 50 missing → rudder_partial_infrastructure_failure MEDIUM
- 0 TOOL_CALL(Task) → no anomaly (quiet)

---

## Q2. Cross-detector aggregation shape

### Problem

Brief §5.5 specified two views:
1. By window, then by detector ("Between 10:00 and 11:00, 3 detectors fired")
2. By detector family, then by window ("position-zero fired in 4 of the last 7 daily windows")

Plus a "concurrent HIGH" top-of-report section for same-window HIGH fires.

`format_report` currently produces a single-window report. PR-1b needs to decide implementation:

**Option A: Run detectors per-window and stitch.**

```python
def format_report(windows=None):
    windows = windows or [3600, 86400, 604800]  # 1hr/1day/1week
    by_window = {w: detect_anomalies(w) for w in windows}
    by_detector = invert(by_window)
    return render(by_window, by_detector)
```

Runs each detector N times (once per window). More flexibility; higher cost.

**Option B: Run detectors once at widest window, partition results by timestamp.**

```python
def format_report(windows=None):
    all_anomalies = detect_anomalies(widest_window)
    by_window = {w: [a for a in all_anomalies if a.fires_within(w)] for w in windows}
```

One detector-pass; O(1) in window count. Requires anomalies to carry a fired-at timestamp so they can be windowed post-hoc.

### Decision

**Option A — run per-window and stitch.** Rationale:

- Some detectors aren't window-partitionable. Variance-collapse over 1 week is not just "variance-collapse over 1 day counted 7 times" — the math is different. Running once at the widest window and post-hoc filtering would silently produce wrong answers for variance-collapse and content-entropy.
- Window count is small (2 in v2.1: 1day/1week). 2× cost, not 7×.
- Simpler data flow: each window is a complete detection run.
- Cost is bounded: detect_anomalies is O(n) in ledger events, window size limits n, small constant.

**Implementation:**

```python
def format_report(
    windows: tuple[float, ...] = (86400.0, 604800.0),
    now: float | None = None,
) -> str:
    per_window: dict[float, list[Anomaly]] = {}
    for w in windows:
        per_window[w] = detect_anomalies(window_seconds=w, now=now)

    # Aggregation
    by_detector: dict[str, list[tuple[float, Anomaly]]] = {}
    for w, anomalies in per_window.items():
        for a in anomalies:
            by_detector.setdefault(a.name, []).append((w, a))

    # Concurrent-HIGH check: HIGH-severity anomalies firing in the same window
    concurrent_high: dict[float, list[Anomaly]] = {}
    for w, anomalies in per_window.items():
        highs = [a for a in anomalies if a.severity == AnomalySeverity.HIGH]
        if len(highs) >= 2:
            concurrent_high[w] = highs

    return _render_report(per_window, by_detector, concurrent_high)
```

The rendered report has three sections (in order):

1. **Concurrent HIGH (top, if any)** — loud-in-experience placement for the strongest signal class
2. **By window** — default listing; each window block shows detectors that fired
3. **By detector** — appendix showing which detectors are chronic across windows

Keeps the brief's two-view semantics without data-flow gymnastics.

### Multi-window meta-detector interaction

Brief §2c specified the meta-detector fires when a primary detector fires in **both** 1day and 1week windows. With Option A, that's natural: after per-window runs, check `by_detector` — any detector name with anomalies in both the 1day and 1week bucket is a multi-window fire.

```python
multi_window = {
    name for name, fires in by_detector.items()
    if {w for w, _ in fires} == set(windows)
}
```

Emitted as its own anomaly entry in the report with elevated severity (LOW→MEDIUM, MEDIUM→HIGH, HIGH stays HIGH).

---

## Q3 (new): Event emission shape

Brief §5 response-path matrix specifies HIGH-severity detectors emit `COMPLIANCE_DRIFT_HIGH` events. Infrastructure-failure emits its own class per brief §3.

### Decision

One emission point inside `detect_anomalies` after the anomaly list is built:

```python
def detect_anomalies(...) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    # ... run all detectors, fill anomalies ...

    # v2.1 §5: HIGH-severity anomalies emit a ledger event for forensic
    # retention. Separate event classes per brief §3.
    for a in anomalies:
        if a.severity == AnomalySeverity.HIGH:
            if a.name == "rudder_infrastructure_failure":
                event_type = "RUDDER_INFRASTRUCTURE_FAILURE"
            else:
                event_type = "COMPLIANCE_DRIFT_HIGH"
            try:
                from divineos.core.ledger import log_event
                log_event(
                    event_type=event_type,
                    actor="compliance_audit",
                    payload={
                        "detector": a.name,
                        "observation": a.observation,
                        "detail": a.detail,
                    },
                    validate=False,
                )
            except Exception:
                pass

    return anomalies
```

Both `COMPLIANCE_DRIFT_HIGH` and `RUDDER_INFRASTRUCTURE_FAILURE` are NOT added to `_COMPRESSIBLE_TYPES` in `ledger_compressor.py` — forensic enforcement records must persist. Parallel to FIRED's retention policy from Item 6.

---

## Scope estimate (PR-1b)

- `compliance_audit.py`: +~300 lines
    - 3 remaining detectors (variance-collapse ~80, content-entropy ~60, multi-window meta ~40)
    - `rudder_infrastructure_failure` + `rudder_partial_infrastructure_failure` via TOOL_CALL read (~60)
    - Event emission (~30)
    - `format_report` rewrite for per-window + aggregation (~50)
- `ledger_compressor.py`: 0 lines (FIRED & COMPLIANCE_DRIFT_HIGH stay out of compressible set by design)
- Tests: +~250 lines across detector-specific tests, emission tests, aggregation tests

Total: ~550 lines across 2 files (compliance_audit + test). Touches guardrailed `compliance_audit.py` — External-Review trailer required.

---

## Pre-reg additions

- **7.1-infra-A** — rudder_infrastructure_failure via TOOL_CALL source
    - success: synthetic scenario (100 gated TOOL_CALL, 0 rudder events) → HIGH anomaly
    - falsifier: >15% FPR across 30 days live data
    - FN check: monthly synthetic fire test; 3 months zero → audit flag
- **7.1-infra-B** — rudder_partial_infrastructure_failure (<80% coverage)
    - success: 100 gated, 50 events → MEDIUM
    - falsifier: >20% FPR on legitimate partial-hook-failure recovery sessions
- **7.4 event-emission** — HIGH anomalies produce ledger events
    - success: each HIGH anomaly detected produces exactly one `COMPLIANCE_DRIFT_HIGH` or `RUDDER_INFRASTRUCTURE_FAILURE` event
    - falsifier: HIGH anomaly detected but no matching ledger event

---

## What fresh-Claude is asked to review

1. **Q1 decision (Option C, TOOL_CALL as active-session source)** — is this the right call, or does A/B have an advantage I missed?
2. **Q1 bonus detector** — is `rudder_partial_infrastructure_failure` worth its complexity, or should PR-1b ship binary (full failure only) and add partial later?
3. **Q2 decision (Option A, per-window run)** — non-partitionable detectors (variance, entropy) justify 2× cost. Agree?
4. **Q2 report layout** — concurrent-HIGH first, by-window default, by-detector appendix. Right ordering for operator cognitive load?
5. **Q3 emission shape** — one emission point after all detectors run, separate event classes per brief §3. Anything to name as NOT-emitted (e.g., MEDIUM fires today but might warrant emission later)?
6. **Pre-reg additions 7.1-infra-A/B/4** — falsifiers distinct enough?

After approval, PR-1b implementation proceeds with precommit-first discipline.
