#!/usr/bin/env python3
"""Comprehensive retroactive audit of TESTED+ knowledge entries.

Audit r9-21 round-3+ (prereg-0c581f89418a). Every TESTED+ knowledge
entry currently in the substrate was promoted under weaker discipline
than current — the full present-day gate stack didn't exist when
those entries were promoted.

This script walks every TESTED+ entry and simulates each current gate
against the entry's current state. It produces a per-entry verdict
and surfaces candidates that would not pass today's full discipline.

Gates simulated (in the same order as promote_maturity):

  1. **Validity gate** (``_passes_validity_gate``) — re-runs the
     warrant-based validity check that was added/tightened over time.
  2. **Unified-frame gate** (``check_promotion_gate``) — would the
     entry pass the seductive-elegance check? Already covered by
     ``audit_unified_frame_backfill.py`` but included here for
     completeness.
  3. **EMPIRICA gate** (``evaluate_and_issue``) — simulates whether
     the entry would receive a receipt under today's evidence-burden
     discipline. The most architecturally significant retroactive
     check, since EMPIRICA is brand new and gated zero past
     promotions.

This script SURFACES; it does not modify any entry. Same shape as
the orphan-triage and unified-frame-backfill audits.

Usage:

    python scripts/audit_retroactive_promotion.py
    python scripts/audit_retroactive_promotion.py --json
    python scripts/audit_retroactive_promotion.py --maturity CONFIRMED
    python scripts/audit_retroactive_promotion.py --no-empirica  (skip embedding-dep gate)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))


def _walk_entries(maturity_filter: str | None) -> list[dict]:
    """Fetch all TESTED or CONFIRMED entries (active, not superseded)."""
    from divineos.core._ledger_base import get_connection

    target_maturities: tuple[str, ...]
    if maturity_filter:
        target_maturities = (maturity_filter,)
    else:
        target_maturities = ("TESTED", "CONFIRMED")

    placeholders = ",".join("?" for _ in target_maturities)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT knowledge_id, knowledge_type, maturity, corroboration_count, "
            "confidence, content, source, tags, created_at "
            f"FROM knowledge WHERE maturity IN ({placeholders}) "  # nosec B608 — placeholders from closed allowlist
            "AND superseded_by IS NULL",
            target_maturities,
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "knowledge_id": r[0],
            "knowledge_type": r[1] or "",
            "maturity": r[2],
            "corroboration_count": r[3] or 0,
            "confidence": r[4] or 0.0,
            "content": r[5] or "",
            "source": r[6] or "",
            "tags": r[7] or "[]",
            "created_at": float(r[8]) if r[8] else 0.0,
        }
        for r in rows
    ]


def _simulate_validity_gate(entry: dict) -> tuple[bool, str]:
    """Simulate the warrant-based validity gate against entry's current state.

    The gate's signature is _passes_validity_gate(knowledge_id,
    from_maturity, to_maturity, corroboration_count). We simulate the
    "would this entry have passed the gate at the moment it was
    promoted to its current maturity" question by treating its current
    state as the moment-of-promotion target.
    """
    from divineos.core.knowledge_maintenance import _passes_validity_gate

    # Simulate the most recent transition implied by current maturity:
    # CONFIRMED implies TESTED->CONFIRMED, TESTED implies HYPOTHESIS->TESTED.
    if entry["maturity"] == "CONFIRMED":
        prior, target = "TESTED", "CONFIRMED"
    elif entry["maturity"] == "TESTED":
        prior, target = "HYPOTHESIS", "TESTED"
    else:
        return True, "non-promoting maturity (no validity check applies)"

    try:
        passes = _passes_validity_gate(
            entry["knowledge_id"],
            prior,
            target,
            entry["corroboration_count"],
        )
    except Exception as e:  # noqa: BLE001 — defensive against any gate-internal error
        return False, f"validity gate raised: {type(e).__name__}: {e}"

    if passes:
        return True, f"validity gate would pass {prior}->{target}"
    return (
        False,
        f"validity gate would BLOCK {prior}->{target} at current corroboration={entry['corroboration_count']}",
    )


def _simulate_unified_frame_gate(entry: dict) -> tuple[bool, str]:
    """Simulate the unified-frame gate."""
    from divineos.core.knowledge._unified_frame import check_promotion_gate

    if entry["maturity"] == "CONFIRMED":
        target = "CONFIRMED"
    elif entry["maturity"] == "TESTED":
        target = "TESTED"
    else:
        return True, "non-promoting maturity"

    ok, reason = check_promotion_gate(entry["knowledge_id"], entry["content"], target)
    return ok, reason or "unified-frame gate passes (not unified-shape)"


def _simulate_empirica_gate(entry: dict) -> tuple[bool, str]:
    """Simulate the EMPIRICA evidence-burden gate.

    EMPIRICA only applies on TESTED->CONFIRMED transitions. So this
    only meaningfully evaluates CONFIRMED entries — TESTED entries
    haven't crossed that boundary yet (current discipline) so they
    don't have an EMPIRICA gate to retroactively apply.
    """
    if entry["maturity"] != "CONFIRMED":
        return True, "EMPIRICA only gates TESTED->CONFIRMED transition"

    try:
        from divineos.core.empirica.gate import evaluate_and_issue
    except ImportError as e:
        return True, f"EMPIRICA module unavailable (skipped): {e}"

    try:
        receipt, classification, _ = evaluate_and_issue(
            claim_id=entry["knowledge_id"],
            content=entry["content"],
            corroboration_count=entry["corroboration_count"],
            knowledge_type=entry["knowledge_type"],
            source=entry["source"],
        )
    except NotImplementedError as e:
        # Tier.ADVERSARIAL raises NotImplementedError per gate docstring
        return True, f"EMPIRICA classified as adversarial (not gated in Phase 1): {e}"
    except Exception as e:  # noqa: BLE001 — defensive
        return False, f"EMPIRICA gate raised: {type(e).__name__}: {e}"

    if receipt is not None:
        return True, (
            f"EMPIRICA would issue receipt: tier={classification.tier.value}, "
            f"magnitude={classification.magnitude.name}"
        )
    return False, (
        f"EMPIRICA would NOT issue receipt: tier={classification.tier.value}, "
        f"magnitude={classification.magnitude.name}, "
        f"corroboration={entry['corroboration_count']} insufficient for tier+magnitude"
    )


def audit_entry(entry: dict, run_empirica: bool = True) -> dict:
    """Run all gates against one entry. Returns a verdict dict."""
    validity_ok, validity_reason = _simulate_validity_gate(entry)
    unified_ok, unified_reason = _simulate_unified_frame_gate(entry)
    if run_empirica:
        empirica_ok, empirica_reason = _simulate_empirica_gate(entry)
    else:
        empirica_ok, empirica_reason = True, "EMPIRICA simulation skipped by flag"

    overall_ok = validity_ok and unified_ok and empirica_ok

    return {
        "knowledge_id": entry["knowledge_id"],
        "knowledge_type": entry["knowledge_type"],
        "maturity": entry["maturity"],
        "corroboration_count": entry["corroboration_count"],
        "content_preview": entry["content"][:160],
        "verdict": "PASS" if overall_ok else "WOULD FAIL",
        "gates": {
            "validity": {"pass": validity_ok, "reason": validity_reason},
            "unified_frame": {"pass": unified_ok, "reason": unified_reason},
            "empirica": {"pass": empirica_ok, "reason": empirica_reason},
        },
    }


def format_summary(verdicts: list[dict]) -> str:
    if not verdicts:
        return "[retroactive audit] no entries to audit (substrate has no TESTED+ knowledge)."

    fail_count = sum(1 for v in verdicts if v["verdict"] == "WOULD FAIL")
    pass_count = len(verdicts) - fail_count

    lines = [
        "[retroactive audit] summary:",
        f"  Total TESTED+ entries: {len(verdicts)}",
        f"  Would PASS current gates: {pass_count}",
        f"  Would FAIL current gates: {fail_count} ({100 * fail_count / len(verdicts):.1f}%)",
        "",
    ]

    if fail_count == 0:
        lines.append("  [!] Zero failures across the corpus is suspicious per the prereg")
        lines.append("    falsifier — equivalent past/current discipline is unlikely")
        lines.append("    given new gates. The heuristic may be too lenient.")
        return "\n".join(lines)

    if fail_count > 0.4 * len(verdicts):
        lines.append("  [!] Failure rate above 40% — either substrate corruption is")
        lines.append("    real OR the gates are too strict for retroactive application.")
        lines.append("    Per prereg falsifier, this triggers a calibration review.")
        lines.append("")

    lines.append("Per-gate failure counts:")
    gate_fail_counts = {"validity": 0, "unified_frame": 0, "empirica": 0}
    for v in verdicts:
        if v["verdict"] == "WOULD FAIL":
            for g, info in v["gates"].items():
                if not info["pass"]:
                    gate_fail_counts[g] += 1
    for g, n in gate_fail_counts.items():
        lines.append(f"  {g}: {n} entries would fail")

    lines.append("")
    lines.append("Sample of failing entries (up to 10):")
    failures = [v for v in verdicts if v["verdict"] == "WOULD FAIL"]
    for v in failures[:10]:
        lines.append(
            f"  {v['knowledge_id'][:12]} [{v['knowledge_type']}/{v['maturity']}] "
            f"corr={v['corroboration_count']}"
        )
        for g, info in v["gates"].items():
            if not info["pass"]:
                lines.append(f"    {g}: {info['reason']}")
        lines.append(f"    content: {v['content_preview']!r}")
        lines.append("")
    if len(failures) > 10:
        lines.append(f"  ... and {len(failures) - 10} more (use --json for full list)")

    lines.append("")
    lines.append("For each failing entry, decide:")
    lines.append("  (a) LEGITIMIZE: provide the evidence the current gate requires")
    lines.append("      (e.g. tag with 'council-walk' for unified-frame; add corroborations)")
    lines.append("  (b) DEMOTE: return to a maturity the entry would actually pass")
    lines.append("  (c) SUPERSEDE: write a corrected/disaggregated replacement and link")
    lines.append("")
    lines.append("This script SURFACES; it does not modify. Operator decides per entry.")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--maturity",
        choices=("TESTED", "CONFIRMED"),
        default=None,
        help="Filter to a single maturity (default: both TESTED and CONFIRMED).",
    )
    parser.add_argument(
        "--no-empirica",
        action="store_true",
        help="Skip the EMPIRICA gate simulation (useful when embedding deps absent).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full per-entry verdict list as JSON.",
    )
    args = parser.parse_args()

    entries = _walk_entries(args.maturity)
    verdicts = [audit_entry(e, run_empirica=not args.no_empirica) for e in entries]

    if args.json:
        out = {
            "total": len(verdicts),
            "fail_count": sum(1 for v in verdicts if v["verdict"] == "WOULD FAIL"),
            "verdicts": verdicts,
        }
        print(json.dumps(out, indent=2))
    else:
        print(format_summary(verdicts))

    fail_count = sum(1 for v in verdicts if v["verdict"] == "WOULD FAIL")
    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
