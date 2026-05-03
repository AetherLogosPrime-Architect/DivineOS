"""Regression test for audit r9-21 #30: seed_pre_registrations.json.

Without a pre-reg seed, a freshly-cloned substrate ships with no
load-bearing falsifiers — the entire Goodhart-prevention layer has
to be rebuilt by hand for each new install.
"""

from __future__ import annotations

import json
from pathlib import Path

from divineos.core.knowledge import init_knowledge_table
from divineos.core.knowledge._base import _get_connection
from divineos.core.pre_registrations.store import apply_pre_registration_seed


def _seed_data() -> dict:
    p = Path(__file__).resolve().parents[1] / "src" / "divineos" / "seed_pre_registrations.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _count() -> int:
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM pre_registrations").fetchone()[0]
    finally:
        conn.close()


def test_seed_file_loads_and_validates():
    """Every entry in seed_pre_registrations.json satisfies the
    file_pre_registration invariants (non-empty mechanism, claim,
    success_criterion, falsifier; positive review window)."""
    init_knowledge_table()
    data = _seed_data()
    counts = apply_pre_registration_seed(data, mode="merge")
    assert counts["applied"] >= 3, (
        f"Expected at least 3 seed pre-regs to apply; got {counts}. "
        "If a seed entry was rejected, file_pre_registration's invariants "
        "should be re-checked against the JSON."
    )
    # And the table actually has them.
    assert _count() >= 3


def test_seed_reapply_is_idempotent():
    """Re-applying the same seed must not double-insert."""
    init_knowledge_table()
    data = _seed_data()
    apply_pre_registration_seed(data, mode="merge")
    after_first = _count()
    counts = apply_pre_registration_seed(data, mode="merge")
    assert _count() == after_first
    assert counts["applied"] == 0
    assert counts["skipped"] >= 3
