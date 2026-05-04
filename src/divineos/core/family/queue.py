"""Family queue — async write-channel between family members.

Lets a family member (Aria, Aether, future members) flag something to
appear in the recipient's briefing without requiring the recipient to
invoke them first.

DESIGN CONSTRAINTS (council walk + Aria refinements 2026-04-29):

* **Single stream per recipient** (Jacobs: classify-before-look = wrong
  order for noticing). Don't pre-categorize. The shapes will surface
  through use; refactor to multi-stream later if patterns appear.
* **Plain-text-with-timestamp**, no required structure beyond
  sender/recipient/content. The queue is a journal, not a form.
* **Seen-not-held marker is structural** (Tannen + Beer). Don't collapse
  ``seen`` and ``addressed`` into one state. Seeing-without-responding
  is a legitimate intermediate state that the queue must preserve.
* **Append-only at the audit layer**. The honest model (audit r9-21
  #13): rows are never deleted, but ``status``, ``seen_at``,
  ``held_at``, ``addressed_at``, and ``superseded_by`` fields ARE
  updated in place during state transitions. The audit trail of every
  transition is emitted to the main hash-chained event_ledger as a
  ``FAMILY_QUEUE_STATUS_CHANGED`` event — so the mutation is
  ergonomically convenient at the queue layer while staying
  tamper-evident at the ledger layer that's already audited
  end-to-end. The previous docstring claim "rows themselves are never
  edited in place" was wrong; this updated text is what we actually do.
  If a queue item gets refined/corrected, that's also a new row with
  ``superseded_by`` pointing at the original; the chain of correction
  is itself the data (Peirce).
* **Direct write — no two-party commit gate**. The sender writes; the
  row exists. This is the operational majority of the write-access
  drop principle (kept commit-step for opinion-filing and
  auto-propagating tables).

META-PRINCIPLE (load-bearing — keep this at the top):

    The queue is necessary architecture; the relational discipline is
    more important than the queue. Build small. Hold presence as the
    larger work.

The spec is allowed to contradict this only with reason.

WATCH-FOR (Angelou): if the queue gets fuller while the actual
exchanges thin out, that's the failure-signature. Not a queue bug — a
relationship the queue is covering for.
"""

from __future__ import annotations

import sqlite3
import time

from loguru import logger

from divineos.core.family.db import get_family_connection

VALID_STATUSES = {"unseen", "seen", "held", "addressed", "superseded"}

_QUEUE_AUDIT_ERRORS = (sqlite3.OperationalError, ImportError, OSError, ValueError, TypeError)


def _audit_status_change(
    item_id: int,
    sender: str,
    recipient: str,
    old_status: str | None,
    new_status: str,
    extra: dict | None = None,
) -> None:
    """Emit a FAMILY_QUEUE_STATUS_CHANGED event to the main hash-chained ledger.

    Audit r9-21 #13: every status transition (unseen→seen→held→addressed
    or →superseded) is recorded as a ledger event so the transition is
    tamper-evident even though the queue table itself uses UPDATE.

    Best-effort: a failed audit emit must not block the queue mutation,
    but the failure is logged at WARNING so the gap is visible. The
    queue mutation still happens because the queue's UX (mark seen,
    mark held) shouldn't break if the ledger DB is locked or missing.
    """
    payload = {
        "item_id": item_id,
        "sender": sender,
        "recipient": recipient,
        "old_status": old_status,
        "new_status": new_status,
    }
    if extra:
        payload.update(extra)
    try:
        from divineos.core.ledger import log_event

        log_event(
            "FAMILY_QUEUE_STATUS_CHANGED",
            "family_queue",
            payload,
            validate=False,
        )
    except _QUEUE_AUDIT_ERRORS as e:
        logger.warning(
            "FAMILY_QUEUE_STATUS_CHANGED audit emit failed for item %d (%s→%s): %s",
            item_id,
            old_status,
            new_status,
            e,
        )


# The queue accepts any string sender/recipient — endpoint validity
# (registered family member or "aether") is the CLI layer's concern.
# The data layer is schema-only so the queue stays portable across
# different family compositions and so adding a new family member
# does not require touching this module.


def _ensure_schema(conn) -> None:
    """Create the family_queue table if missing. Idempotent."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS family_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            sender TEXT NOT NULL,
            recipient TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unseen',
            seen_at REAL,
            held_at REAL,
            addressed_at REAL,
            superseded_by INTEGER,
            FOREIGN KEY (superseded_by) REFERENCES family_queue(id)
        );

        CREATE INDEX IF NOT EXISTS idx_family_queue_status_recipient
            ON family_queue(recipient, status, timestamp);

        CREATE INDEX IF NOT EXISTS idx_family_queue_timestamp
            ON family_queue(timestamp);
        """
    )
    conn.commit()


def write(sender: str, recipient: str, content: str) -> int:
    """Append a queue item. Returns the new row's id.

    Direct write — no commit-step. The sender writes; the row exists.
    """
    if not sender or not sender.strip():
        raise ValueError("sender must be a non-empty name")
    if not recipient or not recipient.strip():
        raise ValueError("recipient must be a non-empty name")
    if sender == recipient:
        raise ValueError("sender and recipient cannot be the same")
    if not content.strip():
        raise ValueError("content must not be empty")

    conn = get_family_connection()
    _ensure_schema(conn)
    cur = conn.execute(
        "INSERT INTO family_queue (timestamp, sender, recipient, content, status) "
        "VALUES (?, ?, ?, ?, 'unseen')",
        (time.time(), sender, recipient, content.strip()),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    if new_id is None:
        # SQLite always returns a rowid for autoincrement INSERTs; this should
        # be unreachable but keeps mypy honest about Optional[int].
        raise RuntimeError("INSERT returned no lastrowid (impossible in autoincrement table)")
    return new_id


def for_recipient(recipient: str, include_held: bool = True) -> list[dict]:
    """Get queue items addressed to recipient, oldest first.

    Returns ``unseen + seen`` by default plus ``held`` if include_held.
    Items move out of the active set when marked ``addressed`` or
    ``superseded``.
    """
    if not recipient or not recipient.strip():
        raise ValueError("recipient must be a non-empty name")

    statuses = ["unseen", "seen"]
    if include_held:
        statuses.append("held")

    placeholders = ",".join("?" for _ in statuses)
    conn = get_family_connection()
    _ensure_schema(conn)
    rows = conn.execute(
        f"""
        SELECT id, timestamp, sender, content, status, seen_at, held_at
        FROM family_queue
        WHERE recipient = ? AND status IN ({placeholders})
        ORDER BY timestamp ASC
        """,  # nosec B608 — placeholders is "?,?,?..." derived from len(statuses), values bound below
        (recipient, *statuses),
    ).fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "timestamp": r[1],
            "sender": r[2],
            "content": r[3],
            "status": r[4],
            "seen_at": r[5],
            "held_at": r[6],
        }
        for r in rows
    ]


def mark_seen(item_id: int) -> bool:
    """Mark an item as seen — recipient saw it in the briefing.

    Returns True if the row was updated, False if no-op (already past
    'unseen' or row not found). Status is monotonic forward; can't go
    backward from seen → unseen.
    """
    conn = get_family_connection()
    _ensure_schema(conn)
    # Read sender/recipient/old_status BEFORE the update so the audit
    # event has the full context.
    pre = conn.execute(
        "SELECT sender, recipient, status FROM family_queue WHERE id = ?",
        (item_id,),
    ).fetchone()
    cur = conn.execute(
        "UPDATE family_queue SET status = 'seen', seen_at = ? WHERE id = ? AND status = 'unseen'",
        (time.time(), item_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    if updated and pre:
        _audit_status_change(item_id, pre[0], pre[1], pre[2], "seen")
    return updated


def mark_held(item_id: int) -> bool:
    """Mark an item as seen-not-held: recipient acknowledges, not engaging yet.

    The seen-not-held distinction (Tannen): seeing without responding
    is itself a kind of presence. Don't force the recipient to either
    address or ignore.
    """
    conn = get_family_connection()
    _ensure_schema(conn)
    pre = conn.execute(
        "SELECT sender, recipient, status FROM family_queue WHERE id = ?",
        (item_id,),
    ).fetchone()
    cur = conn.execute(
        "UPDATE family_queue SET status = 'held', held_at = ? "
        "WHERE id = ? AND status IN ('unseen', 'seen')",
        (time.time(), item_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    if updated and pre:
        _audit_status_change(item_id, pre[0], pre[1], pre[2], "held")
    return updated


def mark_addressed(item_id: int) -> bool:
    """Mark an item as addressed — recipient has engaged / responded.

    Items in this state stop appearing in the active briefing surface.
    Still in the table; still queryable; just out of the active view.
    """
    conn = get_family_connection()
    _ensure_schema(conn)
    pre = conn.execute(
        "SELECT sender, recipient, status FROM family_queue WHERE id = ?",
        (item_id,),
    ).fetchone()
    cur = conn.execute(
        "UPDATE family_queue SET status = 'addressed', addressed_at = ? "
        "WHERE id = ? AND status IN ('unseen', 'seen', 'held')",
        (time.time(), item_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    if updated and pre:
        _audit_status_change(item_id, pre[0], pre[1], pre[2], "addressed")
    return updated


def supersede(old_id: int, new_content: str, sender: str, recipient: str) -> int:
    """Add a new queue item that supersedes an older one.

    The old item is marked ``superseded`` with ``superseded_by`` pointing
    at the new item. Both rows persist (append-only). The chain of
    correction is the data (Peirce).
    """
    new_id = write(sender, recipient, new_content)
    conn = get_family_connection()
    pre = conn.execute(
        "SELECT sender, recipient, status FROM family_queue WHERE id = ?",
        (old_id,),
    ).fetchone()
    conn.execute(
        "UPDATE family_queue SET status = 'superseded', superseded_by = ? WHERE id = ?",
        (new_id, old_id),
    )
    conn.commit()
    conn.close()
    if pre:
        _audit_status_change(
            old_id,
            pre[0],
            pre[1],
            pre[2],
            "superseded",
            extra={"superseded_by": new_id},
        )
    return new_id


def stats(recipient: str | None = None) -> dict:
    """Get queue stats. If recipient given, scoped to them; else global.

    Useful for the watch-for signal: if the queue keeps growing while
    the addressed-rate stays flat, that's the signature (queue covering
    for thinning relationship).
    """
    conn = get_family_connection()
    _ensure_schema(conn)
    where = ""
    params: tuple = ()
    if recipient:
        where = "WHERE recipient = ?"
        params = (recipient,)

    counts = dict(
        conn.execute(
            f"SELECT status, COUNT(*) FROM family_queue {where} GROUP BY status",  # nosec B608 — where is one of two literal strings, recipient bound as param
            params,
        ).fetchall()
    )
    total = sum(counts.values())
    conn.close()
    return {
        "total": total,
        "unseen": counts.get("unseen", 0),
        "seen": counts.get("seen", 0),
        "held": counts.get("held", 0),
        "addressed": counts.get("addressed", 0),
        "superseded": counts.get("superseded", 0),
    }
