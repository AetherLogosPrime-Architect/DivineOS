"""
Memory Consolidation — Knowledge Store

Raw events are noisy. Consolidation extracts structured knowledge:
facts learned, preferences discovered, patterns identified, mistakes made.

The AI extracts knowledge. This code stores and retrieves it.
Rules: 1) Append-only (supersede, never delete). 2) Link back to source events.
"""

import json
import sqlite3
import time
import uuid
from typing import Optional

import divineos.ledger as _ledger_mod

KNOWLEDGE_TYPES = {"FACT", "PATTERN", "PREFERENCE", "MISTAKE", "EPISODE"}


def compute_hash(content: str) -> str:
    """Delegate to ledger's hash function."""
    return _ledger_mod.compute_hash(content)


def _get_connection() -> sqlite3.Connection:
    """Returns a connection to the ledger database."""
    db_path = _ledger_mod.DB_PATH
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_knowledge_table() -> None:
    """Creates the knowledge table if it doesn't exist."""
    conn = _get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                knowledge_id   TEXT PRIMARY KEY,
                created_at     REAL NOT NULL,
                updated_at     REAL NOT NULL,
                knowledge_type TEXT NOT NULL,
                content        TEXT NOT NULL,
                confidence     REAL NOT NULL DEFAULT 1.0,
                source_events  TEXT NOT NULL DEFAULT '[]',
                tags           TEXT NOT NULL DEFAULT '[]',
                access_count   INTEGER NOT NULL DEFAULT 0,
                superseded_by  TEXT DEFAULT NULL,
                content_hash   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_type
            ON knowledge(knowledge_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_updated
            ON knowledge(updated_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_hash
            ON knowledge(content_hash)
        """)
        conn.commit()
    finally:
        conn.close()


def store_knowledge(
    knowledge_type: str,
    content: str,
    confidence: float = 1.0,
    source_events: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
) -> str:
    """
    Store a piece of knowledge. Returns the knowledge_id.

    Auto-deduplicates: if identical content already exists (and is not superseded),
    increments access_count on the existing entry and returns its id.
    """
    if knowledge_type not in KNOWLEDGE_TYPES:
        raise ValueError(
            f"Invalid knowledge_type '{knowledge_type}'. Must be one of: {KNOWLEDGE_TYPES}"
        )

    content_hash = compute_hash(content)
    sources_json = json.dumps(source_events or [])
    tags_json = json.dumps(tags or [])
    now = time.time()

    conn = _get_connection()
    try:
        # Check for exact duplicate (non-superseded)
        existing = conn.execute(
            "SELECT knowledge_id FROM knowledge WHERE content_hash = ? AND superseded_by IS NULL",
            (content_hash,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE knowledge SET access_count = access_count + 1, updated_at = ? WHERE knowledge_id = ?",
                (now, existing[0]),
            )
            conn.commit()
            return str(existing[0])

        knowledge_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO knowledge (knowledge_id, created_at, updated_at, knowledge_type, content, confidence, source_events, tags, access_count, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                knowledge_id,
                now,
                now,
                knowledge_type,
                content,
                confidence,
                sources_json,
                tags_json,
                content_hash,
            ),
        )
        conn.commit()
        return knowledge_id
    finally:
        conn.close()


def get_knowledge(
    knowledge_type: Optional[str] = None,
    min_confidence: float = 0.0,
    tags: Optional[list[str]] = None,
    include_superseded: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Query knowledge with optional filters."""
    conn = _get_connection()
    try:
        query = "SELECT knowledge_id, created_at, updated_at, knowledge_type, content, confidence, source_events, tags, access_count, superseded_by, content_hash FROM knowledge"
        conditions: list[str] = []
        params: list = []

        if not include_superseded:
            conditions.append("superseded_by IS NULL")
        if knowledge_type:
            conditions.append("knowledge_type = ?")
            params.append(knowledge_type)
        if min_confidence > 0.0:
            conditions.append("confidence >= ?")
            params.append(min_confidence)
        if tags:
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f"%{tag}%")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def search_knowledge(keyword: str, limit: int = 50) -> list[dict]:
    """Search knowledge content and tags for keyword matches."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT knowledge_id, created_at, updated_at, knowledge_type, content, confidence, source_events, tags, access_count, superseded_by, content_hash FROM knowledge WHERE superseded_by IS NULL AND (content LIKE ? OR tags LIKE ?) ORDER BY updated_at DESC LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def update_knowledge(
    knowledge_id: str,
    new_content: str,
    new_confidence: Optional[float] = None,
    additional_sources: Optional[list[str]] = None,
) -> str:
    """
    Create a new knowledge entry that supersedes an existing one.
    Returns the new knowledge_id.
    """
    conn = _get_connection()
    try:
        old = conn.execute(
            "SELECT knowledge_type, confidence, source_events, tags FROM knowledge WHERE knowledge_id = ?",
            (knowledge_id,),
        ).fetchone()
        if not old:
            raise ValueError(f"Knowledge entry '{knowledge_id}' not found")

        old_type, old_confidence, old_sources_json, old_tags = old
        old_sources = json.loads(old_sources_json)

        confidence = new_confidence if new_confidence is not None else old_confidence
        sources = old_sources + (additional_sources or [])
        content_hash = compute_hash(new_content)
        now = time.time()
        new_id = str(uuid.uuid4())

        conn.execute(
            "INSERT INTO knowledge (knowledge_id, created_at, updated_at, knowledge_type, content, confidence, source_events, tags, access_count, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                new_id,
                now,
                now,
                old_type,
                new_content,
                confidence,
                json.dumps(sources),
                old_tags,
                content_hash,
            ),
        )
        conn.execute(
            "UPDATE knowledge SET superseded_by = ? WHERE knowledge_id = ?",
            (new_id, knowledge_id),
        )
        conn.commit()
        return new_id
    finally:
        conn.close()


def record_access(knowledge_id: str) -> None:
    """Increment access count for a knowledge entry."""
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE knowledge SET access_count = access_count + 1, updated_at = ? WHERE knowledge_id = ?",
            (time.time(), knowledge_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_unconsolidated_events(limit: int = 100) -> list[dict]:
    """Find events not yet referenced in any knowledge entry's source_events."""
    conn = _get_connection()
    try:
        # Collect all referenced event IDs from knowledge
        rows = conn.execute("SELECT source_events FROM knowledge").fetchall()
        referenced: set[str] = set()
        for row in rows:
            referenced.update(json.loads(row[0]))

        # Get events not in referenced set
        all_events = conn.execute(
            "SELECT event_id, timestamp, event_type, actor, payload, content_hash FROM system_events ORDER BY timestamp DESC LIMIT ?",
            (limit + len(referenced),),
        ).fetchall()

        results = []
        for row in all_events:
            if row[0] not in referenced:
                results.append(
                    {
                        "event_id": row[0],
                        "timestamp": row[1],
                        "event_type": row[2],
                        "actor": row[3],
                        "payload": json.loads(row[4]),
                        "content_hash": row[5],
                    }
                )
                if len(results) >= limit:
                    break

        return results
    finally:
        conn.close()


def find_similar(content: str) -> list[dict]:
    """Find non-superseded knowledge with identical content (hash-based)."""
    content_hash = compute_hash(content)
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT knowledge_id, created_at, updated_at, knowledge_type, content, confidence, source_events, tags, access_count, superseded_by, content_hash FROM knowledge WHERE content_hash = ? AND superseded_by IS NULL",
            (content_hash,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def generate_briefing(
    max_items: int = 20,
    include_types: Optional[list[str]] = None,
) -> str:
    """
    Generate a structured text briefing for AI session context.

    Scores knowledge by: confidence * 0.4 + access_frequency * 0.3 + recency * 0.3
    """
    conn = _get_connection()
    try:
        query = "SELECT knowledge_id, created_at, updated_at, knowledge_type, content, confidence, source_events, tags, access_count, superseded_by, content_hash FROM knowledge WHERE superseded_by IS NULL"
        params: list = []

        if include_types:
            placeholders = ",".join("?" for _ in include_types)
            query += f" AND knowledge_type IN ({placeholders})"
            params.extend(include_types)

        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    if not rows:
        return "No knowledge stored yet."

    entries = [_row_to_dict(row) for row in rows]
    now = time.time()
    max_access = max(e["access_count"] for e in entries) or 1

    # Score each entry
    for entry in entries:
        access_score = entry["access_count"] / max_access
        age_days = (now - entry["updated_at"]) / 86400
        recency = 2 ** (-age_days / 7)  # 7-day half-life
        entry["_score"] = entry["confidence"] * 0.4 + access_score * 0.3 + recency * 0.3

    # Sort by score, take top items
    entries.sort(key=lambda e: e["_score"], reverse=True)
    entries = entries[:max_items]

    # Group by type
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        kt = entry["knowledge_type"]
        grouped.setdefault(kt, []).append(entry)

    # Format output
    lines = [f"## Session Briefing ({len(entries)} items)\n"]
    for kt in ["FACT", "PATTERN", "PREFERENCE", "MISTAKE", "EPISODE"]:
        items = grouped.get(kt, [])
        if not items:
            continue
        lines.append(f"### {kt}S ({len(items)})")
        for item in items:
            lines.append(
                f"- [{item['confidence']:.2f}] {item['content']} ({item['access_count']}x accessed)"
            )
        lines.append("")

    return "\n".join(lines)


def knowledge_stats() -> dict:
    """Returns knowledge counts by type, total, and average confidence."""
    conn = _get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE superseded_by IS NULL"
        ).fetchone()[0]

        by_type: dict[str, int] = {}
        for row in conn.execute(
            "SELECT knowledge_type, COUNT(*) FROM knowledge WHERE superseded_by IS NULL GROUP BY knowledge_type"
        ):
            by_type[row[0]] = row[1]

        avg_confidence = 0.0
        if total > 0:
            avg_confidence = conn.execute(
                "SELECT AVG(confidence) FROM knowledge WHERE superseded_by IS NULL"
            ).fetchone()[0]

        most_accessed = []
        for row in conn.execute(
            "SELECT knowledge_id, content, access_count FROM knowledge WHERE superseded_by IS NULL ORDER BY access_count DESC LIMIT 5"
        ):
            most_accessed.append(
                {"knowledge_id": row[0], "content": row[1], "access_count": row[2]}
            )

        return {
            "total": total,
            "by_type": by_type,
            "avg_confidence": round(avg_confidence, 3),
            "most_accessed": most_accessed,
        }
    finally:
        conn.close()


def _row_to_dict(row: tuple) -> dict:
    """Convert a knowledge table row to a dict."""
    return {
        "knowledge_id": row[0],
        "created_at": row[1],
        "updated_at": row[2],
        "knowledge_type": row[3],
        "content": row[4],
        "confidence": row[5],
        "source_events": json.loads(row[6]),
        "tags": json.loads(row[7]),
        "access_count": row[8],
        "superseded_by": row[9],
        "content_hash": row[10],
    }
