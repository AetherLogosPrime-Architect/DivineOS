"""Regression test for audit r9-21 #29: seed_key field for content drift.

The bug:
  apply_seed dedups by lowercase content. If a seed entry's wording
  was tweaked (typo fix, comma added), the same seed produced a
  duplicate insert because content.lower() didn't match.

The fix:
  Optional ``key`` field per seed entry. Stamped to the row's seed_key
  column on first apply. On subsequent applies, dedup by seed_key
  (independent of content drift).
"""

from __future__ import annotations

from divineos.core.knowledge import init_knowledge_table
from divineos.core.knowledge._base import _get_connection
from divineos.core.memory import init_memory_tables
from divineos.core.seed_manager import apply_seed


def _count_rows() -> int:
    conn = _get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
    finally:
        conn.close()


def _row_with_seed_key(key: str):
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT knowledge_id, content, seed_key FROM knowledge "
            "WHERE seed_key = ? AND superseded_by IS NULL",
            (key,),
        ).fetchone()
    finally:
        conn.close()


def test_seed_key_dedups_across_content_drift():
    """Re-apply with drifted content + same key → no duplicate insert."""
    init_memory_tables()
    init_knowledge_table()

    seed_v1 = {
        "knowledge": [
            {
                "type": "PRINCIPLE",
                "content": "Always read the file before editing.",
                "key": "principle/read_before_edit",
            }
        ]
    }
    seed_v2 = {
        "knowledge": [
            {
                "type": "PRINCIPLE",
                # Same idea, drifted wording (added "thoroughly").
                "content": "Always read the file thoroughly before editing.",
                "key": "principle/read_before_edit",
            }
        ]
    }

    apply_seed(seed_v1, mode="merge")
    count_after_v1 = _count_rows()
    row1 = _row_with_seed_key("principle/read_before_edit")
    assert row1 is not None, "seed_key not stamped on first apply"

    # Re-apply v2 with drifted wording — should NOT add a duplicate.
    counts = apply_seed(seed_v2, mode="merge")
    assert _count_rows() == count_after_v1, (
        "Drifted content with same seed_key produced a duplicate insert. "
        "Audit r9-21 #29 fix didn't take."
    )
    assert counts["skipped"] >= 1


def test_seed_without_key_falls_back_to_content_dedup():
    """Entries without a key still dedup by content (back-compat)."""
    init_memory_tables()
    init_knowledge_table()

    seed = {
        "knowledge": [
            {
                "type": "FACT",
                "content": "DivineOS uses SQLite for persistence.",
            }
        ]
    }
    apply_seed(seed, mode="merge")
    before = _count_rows()
    apply_seed(seed, mode="merge")
    assert _count_rows() == before, (
        "Identical content without seed_key should still dedup by content."
    )
