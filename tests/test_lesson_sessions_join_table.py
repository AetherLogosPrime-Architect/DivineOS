"""Regression test for audit r9-21 #25: lesson_tracking JSON→join table.

The bug:
  ``lesson_tracking.sessions`` was a JSON-encoded TEXT column. Every
  ``is_new_session`` check parsed the JSON; cross-lesson queries
  ("which lessons appeared in session X") required a full table scan
  with per-row JSON deserialization. SQL aggregates were impossible.

The fix:
  ``lesson_sessions(lesson_id, session_id, observed_at, PRIMARY KEY)``
  join table. record_lesson writes via INSERT OR IGNORE, eliminating
  the read-modify-write race on the JSON column. SQL aggregates now
  work directly. The JSON column is preserved (dual-written) for
  back-compat readers.
"""

from __future__ import annotations

from divineos.core.knowledge import init_knowledge_table
from divineos.core.knowledge._base import _get_connection
from divineos.core.knowledge.lessons import record_lesson


def _join_count(lesson_id: str) -> int:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM lesson_sessions WHERE lesson_id = ?",
            (lesson_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def _occurrences(category: str) -> int:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT occurrences FROM lesson_tracking WHERE category = ?",
            (category,),
        ).fetchone()[0]
    finally:
        conn.close()


def test_join_table_dedups_within_session():
    """Recording the same lesson twice in one session must not double-count."""
    init_knowledge_table()
    lid = record_lesson("test/dedup-cat", "test desc", session_id="sess-A")
    record_lesson("test/dedup-cat", "test desc", session_id="sess-A")
    record_lesson("test/dedup-cat", "test desc", session_id="sess-A")
    assert _join_count(lid) == 1, (
        "Same (lesson, session) pair must collapse to 1 row in join table."
    )
    assert _occurrences("test/dedup-cat") == 1


def test_join_table_records_distinct_sessions():
    """Distinct sessions each produce a row."""
    init_knowledge_table()
    lid = record_lesson("test/multi-session-cat", "test desc", session_id="sess-X")
    record_lesson("test/multi-session-cat", "test desc", session_id="sess-Y")
    record_lesson("test/multi-session-cat", "test desc", session_id="sess-Z")
    assert _join_count(lid) == 3
    assert _occurrences("test/multi-session-cat") == 3


def test_sql_aggregate_query_works():
    """The whole point: SQL aggregates across the join table."""
    init_knowledge_table()
    record_lesson("cat-1", "d1", session_id="shared-sess")
    record_lesson("cat-2", "d2", session_id="shared-sess")
    record_lesson("cat-3", "d3", session_id="other-sess")

    conn = _get_connection()
    try:
        # How many lessons appeared in 'shared-sess'?
        n = conn.execute(
            "SELECT COUNT(DISTINCT lesson_id) FROM lesson_sessions WHERE session_id = ?",
            ("shared-sess",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 2, (
        "SQL aggregate over join table should find both lessons that appeared "
        "in shared-sess. If this fails, the migration didn't take."
    )
