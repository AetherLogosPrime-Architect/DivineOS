#!/usr/bin/env python3
"""Retroactive audit of unified-frame entries promoted before the gate landed.

Audit r9-21 round-3+ (prereg-a95f0f5c0b3c). The unified-frame promotion
gate (prereg-a8e2f3f06fbe) only defends FUTURE promotions. Knowledge
entries already at TESTED or CONFIRMED maturity pre-date the gate and
may have propagated wrong-but-elegant frames as load-bearing patterns.

Per the pattern-recursion lesson (knowledge 6e929fe6): a wrong-but-
elegant pattern occupying the slot a right-but-messy one should fill
propagates — every new pattern gets evaluated against it and inherits
its frame. So every TESTED+ unified-frame entry without council-walk
evidence is a potential corruption surface.

This script audits-and-surfaces. It does NOT auto-modify any entry.

For each candidate surfaced, the operator must decide:

  (a) Legitimize: the entry IS unified-frame but the unification is
      sound, AND a council walk has been done (or should be done).
      Tag with 'council-walk' or 'council-reviewed' to record the
      decision. Use:
          divineos knowledge tag <id> council-walk

  (b) Demote: the unification was elegance-eating-friction. Demote
      back to HYPOTHESIS so it doesn't propagate as load-bearing.
      Demote via:
          divineos admin demote-knowledge <id> HYPOTHESIS
      (this command may not exist yet; sql is the manual path)

  (c) Reframe: the entry's content can be rewritten to disaggregate
      the unification into multiple distinct entries. Then supersede
      the original.

Usage:

    python scripts/audit_unified_frame_backfill.py
    python scripts/audit_unified_frame_backfill.py --json   # machine-readable
    python scripts/audit_unified_frame_backfill.py --maturity TESTED
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))


def _candidate_entries(maturity_filter: str | None) -> list[dict]:
    """Return all entries at TESTED or CONFIRMED that match the unified-frame
    heuristic but lack council-walk evidence."""
    from divineos.core._ledger_base import get_connection
    from divineos.core.knowledge._unified_frame import (
        has_council_walk_evidence,
        is_unified_frame,
    )

    target_maturities: tuple[str, ...]
    if maturity_filter:
        target_maturities = (maturity_filter,)
    else:
        target_maturities = ("TESTED", "CONFIRMED")

    candidates: list[dict] = []
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in target_maturities)
        rows = conn.execute(
            "SELECT knowledge_id, knowledge_type, maturity, corroboration_count, "
            "content, tags, created_at "
            f"FROM knowledge WHERE maturity IN ({placeholders}) "  # nosec B608 — placeholders from closed maturity allowlist
            "AND superseded_by IS NULL",
            target_maturities,
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        kid, ktype, maturity, corr, content, tags, created_at = row
        if not content:
            continue
        is_uni, reason = is_unified_frame(content)
        if not is_uni:
            continue
        if has_council_walk_evidence(kid):
            continue
        candidates.append(
            {
                "knowledge_id": kid,
                "knowledge_type": ktype,
                "maturity": maturity,
                "corroboration_count": corr,
                "content_preview": content[:200],
                "tags": tags or "[]",
                "created_at": float(created_at),
                "heuristic_reason": reason,
            }
        )
    return candidates


def _format_human(candidates: list[dict]) -> str:
    if not candidates:
        return (
            "[unified-frame audit] no candidates found.\n"
            "  All TESTED+ entries either don't match the unified-frame heuristic\n"
            "  or already have council-walk evidence. The substrate is clean against\n"
            "  the prereg-a8e2f3f06fbe falsifier (no unified-frame entry promoted\n"
            "  past HYPOTHESIS without council-walk)."
        )
    lines = [f"[unified-frame audit] {len(candidates)} candidate(s) for review:\n"]
    for i, c in enumerate(candidates, start=1):
        lines.append(
            f"--- Candidate {i}/{len(candidates)} ---"
            f"\n  knowledge_id:      {c['knowledge_id']}"
            f"\n  knowledge_type:    {c['knowledge_type']}"
            f"\n  current maturity:  {c['maturity']}"
            f"\n  corroborations:    {c['corroboration_count']}"
            f"\n  heuristic reason:  {c['heuristic_reason']}"
            f"\n  content (preview): {c['content_preview']!r}"
        )
    lines.append(
        "\nFor each candidate, decide:"
        "\n  (a) LEGITIMIZE: tag with 'council-walk' if the unification is sound"
        "\n      and a council walk has been recorded (or do one)."
        "\n  (b) DEMOTE:     return to HYPOTHESIS if the unification was"
        "\n      elegance-eating-friction (the slip the gate exists to catch)."
        "\n  (c) REFRAME:    rewrite as distinct entries and supersede the"
        "\n      original."
        "\n\nThis script SURFACES; it does not modify. Operator decides per entry."
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--maturity",
        choices=("TESTED", "CONFIRMED"),
        default=None,
        help="Filter to a specific maturity (default: both).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-formatted output.",
    )
    args = parser.parse_args()

    candidates = _candidate_entries(args.maturity)
    if args.json:
        print(json.dumps({"candidates": candidates, "count": len(candidates)}, indent=2))
    else:
        print(_format_human(candidates))
    return 1 if candidates else 0


if __name__ == "__main__":
    sys.exit(main())
