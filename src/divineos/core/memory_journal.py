"""Personal Journal — The AI's own memory.

Things the AI chooses to remember, not filtered or scored.
Save, list, search, link, and count journal entries.
"""

import re
import time
import uuid
from typing import Any, cast

from divineos.core.memory import _get_connection, init_memory_tables


def journal_save(
    content: str,
    context: str = "",
    tags: str = "",
    linked_knowledge_id: str | None = None,
    private: bool = False,
) -> str:
    """Save a personal journal entry. Returns the entry ID.

    This is the AI's own memory — things it chooses to remember,
    not filtered or scored. If it matters to me, that's enough.

    Audit r9-21 #12: ``private=True`` flags the entry as excluded
    from default FTS5 search. ``divineos ask`` (which calls
    journal_search) won't surface private reflections unless the
    caller explicitly passes ``include_private=True``.
    """
    init_memory_tables()
    entry_id = str(uuid.uuid4())
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO personal_journal "
            "(entry_id, content, created_at, context, tags, linked_knowledge_id, private) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                content,
                time.time(),
                context,
                tags,
                linked_knowledge_id,
                1 if private else 0,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return entry_id


def journal_list(limit: int = 20, include_private: bool = False) -> list[dict[str, Any]]:
    """Get personal journal entries, newest first.

    Audit r9-21 #12: ``include_private`` defaults False — same shape
    as journal_search. The default user-facing ``divineos journal list``
    won't surface private entries unless the operator passes the
    --private flag at the CLI.
    """
    init_memory_tables()
    conn = _get_connection()
    privacy_clause = "" if include_private else "WHERE COALESCE(private, 0) = 0 "
    try:
        rows = conn.execute(
            "SELECT entry_id, content, created_at, context, tags, linked_knowledge_id "
            f"FROM personal_journal {privacy_clause}"  # nosec B608 — privacy_clause is one of two literal fragments
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "entry_id": r[0],
            "content": r[1],
            "created_at": r[2],
            "context": r[3],
            "tags": r[4] if len(r) > 4 else "",
            "linked_knowledge_id": r[5] if len(r) > 5 else None,
        }
        for r in rows
    ]


def journal_count() -> int:
    """Count personal journal entries."""
    init_memory_tables()
    conn = _get_connection()
    try:
        return cast(int, conn.execute("SELECT COUNT(*) FROM personal_journal").fetchone()[0])
    finally:
        conn.close()


def _build_fts_or_query(query: str) -> str:
    """Convert query to OR-joined FTS5 terms for partial-match recall."""
    words = [w for w in re.sub(r"[^a-zA-Z0-9\s]", " ", query).lower().split() if len(w) > 1]
    if not words:
        return query
    if len(words) == 1:
        return words[0]
    return " OR ".join(words)


def journal_search(
    query: str,
    limit: int = 10,
    include_private: bool = False,
) -> list[dict[str, Any]]:
    """Full-text search across journal entries using FTS5.

    Audit r9-21 #12: ``include_private`` defaults False so private
    reflections are excluded from default search. ``divineos ask``
    must NOT pass include_private=True silently — this is the gate
    that keeps private entries out of generic knowledge searches.
    Callers that legitimately need private content (e.g. an explicit
    ``divineos journal --private`` listing) pass include_private=True
    deliberately.

    Privacy filter is applied at SELECT time via JOIN to
    personal_journal.private, not at FTS-trigger time. Decision
    rationale recorded as c54ec027 — sidesteps trigger-fragility,
    keeps the FTS index pure.
    """
    init_memory_tables()
    safe_query = _build_fts_or_query(query)
    conn = _get_connection()
    privacy_clause = "" if include_private else "AND COALESCE(j.private, 0) = 0 "
    try:
        rows = conn.execute(
            "SELECT j.entry_id, j.content, j.created_at, j.context, j.tags, j.linked_knowledge_id "
            "FROM journal_fts f "
            "JOIN personal_journal j ON f.rowid = j.rowid "
            f"WHERE journal_fts MATCH ? {privacy_clause}"  # nosec B608 — privacy_clause is one of two literal fragments
            "ORDER BY rank "
            "LIMIT ?",
            (safe_query, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "entry_id": r[0],
            "content": r[1],
            "created_at": r[2],
            "context": r[3],
            "tags": r[4] if len(r) > 4 else "",
            "linked_knowledge_id": r[5] if len(r) > 5 else None,
        }
        for r in rows
    ]


def journal_link(entry_id: str, knowledge_id: str) -> bool:
    """Link a journal entry to a knowledge entry. Returns True if updated."""
    init_memory_tables()
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "UPDATE personal_journal SET linked_knowledge_id = ? WHERE entry_id = ?",
            (knowledge_id, entry_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
