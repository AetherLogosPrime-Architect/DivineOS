"""Warrant persistence and chain verification.

A ``GnosisWarrant`` (see ``types.py``) is the durable record that a
claim passed its tier-appropriate burden check. This module handles
the persistence side: storing warrants in ``gnosis_warrants`` (a new
table in the knowledge DB), fetching the previous warrant to chain
from, and verifying chain integrity across the whole store.

## The chain

Every warrant carries ``previous_warrant_hash`` — the ``self_hash`` of
the warrant issued immediately before it, globally. The first
warrant ever issued has ``previous_warrant_hash = None`` (genesis).
This globally-linked chain mirrors the event ledger's SHA256
hash-chain — tampering with any warrant breaks ``verify_self_hash``
on that warrant AND breaks ``previous_warrant_hash`` references in
all subsequent warrants. The chain is self-auditing.

## Why globally chained, not per-claim

Per-claim chaining would make each claim's warrant history a
separate chain. Simple, but it means tampering with one claim's
history doesn't affect the rest. Globally chained means any
tampering is detectable across all warrants — stronger invariant,
slightly more complex lookup.

Phase 1 uses global chaining. If a future phase needs per-claim
chains on top (e.g., for faster per-claim audit), that can be added
as a secondary index without breaking the global chain.

## What this module is NOT

Not a policy enforcer. ``issue_warrant`` stores a warrant given a
passed burden check. It does not DECIDE whether the burden was met —
that's the caller's job (usually the validity gate in
``knowledge_maintenance`` consulting ``burden.required_corroboration``).
Keeps responsibilities clean and unit-testable.
"""

from __future__ import annotations

import sqlite3

from loguru import logger

from divineos.core._ledger_base import get_connection as _get_ledger_conn
from divineos.core.empirica.types import (
    ClaimMagnitude,
    GnosisWarrant,
    Tier,
    WarrantChainError,
)


def init_warrant_table() -> None:
    """Create the ``gnosis_warrants`` table if missing. Idempotent."""
    conn = _get_ledger_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gnosis_warrants (
                warrant_id             TEXT PRIMARY KEY,
                claim_id               TEXT NOT NULL,
                tier                   TEXT NOT NULL,
                magnitude              INTEGER NOT NULL,
                corroboration_count    INTEGER NOT NULL,
                council_count          INTEGER NOT NULL,
                issued_at              REAL NOT NULL,
                previous_warrant_hash  TEXT,
                self_hash              TEXT NOT NULL UNIQUE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gnosis_warrants_claim ON gnosis_warrants(claim_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gnosis_warrants_issued ON gnosis_warrants(issued_at)"
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        logger.debug(f"gnosis_warrants setup: {e}")
    finally:
        conn.close()


def _latest_warrant_hash() -> str | None:
    """Return the ``self_hash`` of the most recently issued warrant.

    Used to compute ``previous_warrant_hash`` for the next warrant.
    Returns None if no warrants exist yet (the first warrant is
    genesis).
    """
    init_warrant_table()
    conn = _get_ledger_conn()
    try:
        row = conn.execute(
            "SELECT self_hash FROM gnosis_warrants ORDER BY issued_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def issue_warrant(
    claim_id: str,
    tier: Tier,
    magnitude: ClaimMagnitude,
    corroboration_count: int,
    council_count: int = 0,
) -> GnosisWarrant:
    """Issue a warrant for a claim that has passed burden check.

    Caller is responsible for having verified that ``corroboration_count``
    meets ``required_corroboration(tier, magnitude)`` BEFORE calling
    this. This function stores; it does not decide.

    Writes the warrant to ``gnosis_warrants`` with ``previous_warrant_hash``
    automatically set to the latest warrant's ``self_hash`` (chain link).
    Returns the constructed ``GnosisWarrant``.
    """
    init_warrant_table()
    previous_hash = _latest_warrant_hash()
    warrant = GnosisWarrant.issue(
        claim_id=claim_id,
        tier=tier,
        magnitude=magnitude,
        corroboration_count=corroboration_count,
        council_count=council_count,
        previous_warrant_hash=previous_hash,
    )

    conn = _get_ledger_conn()
    try:
        conn.execute(
            """
            INSERT INTO gnosis_warrants (
                warrant_id, claim_id, tier, magnitude, corroboration_count,
                council_count, issued_at, previous_warrant_hash, self_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                warrant.warrant_id,
                warrant.claim_id,
                warrant.tier.value,
                warrant.magnitude.value,
                warrant.corroboration_count,
                warrant.council_count,
                warrant.issued_at,
                warrant.previous_warrant_hash,
                warrant.self_hash,
            ),
        )
        conn.commit()
        logger.info(
            "Issued warrant {} for claim {} (tier={}, magnitude={}, corroboration={})",
            warrant.warrant_id,
            claim_id[:12],
            tier.value,
            magnitude.name,
            corroboration_count,
        )
        return warrant
    finally:
        conn.close()


def get_warrant(warrant_id: str) -> GnosisWarrant | None:
    """Fetch a warrant by ID, or None if not found."""
    init_warrant_table()
    conn = _get_ledger_conn()
    try:
        row = conn.execute(
            "SELECT warrant_id, claim_id, tier, magnitude, corroboration_count, "
            "council_count, issued_at, previous_warrant_hash, self_hash "
            "FROM gnosis_warrants WHERE warrant_id = ?",
            (warrant_id,),
        ).fetchone()
        if not row:
            return None
        return GnosisWarrant(
            warrant_id=row[0],
            claim_id=row[1],
            tier=Tier(row[2]),
            magnitude=ClaimMagnitude(row[3]),
            corroboration_count=int(row[4]),
            council_count=int(row[5]),
            issued_at=float(row[6]),
            previous_warrant_hash=row[7],
            self_hash=row[8],
        )
    finally:
        conn.close()


def get_warrants_for_claim(claim_id: str) -> list[GnosisWarrant]:
    """Return all warrants issued for a given claim, oldest first.

    A single claim may have multiple warrants over time — e.g., a
    supersession event may issue a new warrant on the replacement
    claim with the old claim_id, or a re-corroboration may warrant
    a claim again at higher magnitude. Return order is chronological.
    """
    init_warrant_table()
    conn = _get_ledger_conn()
    try:
        rows = conn.execute(
            "SELECT warrant_id, claim_id, tier, magnitude, corroboration_count, "
            "council_count, issued_at, previous_warrant_hash, self_hash "
            "FROM gnosis_warrants WHERE claim_id = ? ORDER BY issued_at ASC",
            (claim_id,),
        ).fetchall()
        return [
            GnosisWarrant(
                warrant_id=r[0],
                claim_id=r[1],
                tier=Tier(r[2]),
                magnitude=ClaimMagnitude(r[3]),
                corroboration_count=int(r[4]),
                council_count=int(r[5]),
                issued_at=float(r[6]),
                previous_warrant_hash=r[7],
                self_hash=r[8],
            )
            for r in rows
        ]
    finally:
        conn.close()


def verify_chain() -> None:
    """Walk the entire warrant chain and verify integrity.

    Checks two invariants for every warrant:

    1. ``self_hash`` matches the recomputed hash of the warrant's
       current field values (no mid-flight tampering).
    2. ``previous_warrant_hash`` matches the ``self_hash`` of the
       warrant immediately before this one in the chronological
       ordering. Genesis warrant (first ever) is allowed to have
       ``previous_warrant_hash = None``.

    Raises ``WarrantChainError`` on the first integrity failure,
    with a message naming the specific warrant and failure mode.
    Keeps silent on success — the callee doesn't need data back,
    just "did it hold."
    """
    init_warrant_table()
    conn = _get_ledger_conn()
    try:
        rows = conn.execute(
            "SELECT warrant_id, claim_id, tier, magnitude, corroboration_count, "
            "council_count, issued_at, previous_warrant_hash, self_hash "
            "FROM gnosis_warrants ORDER BY issued_at ASC"
        ).fetchall()
    finally:
        conn.close()

    previous_self_hash: str | None = None
    for r in rows:
        warrant = GnosisWarrant(
            warrant_id=r[0],
            claim_id=r[1],
            tier=Tier(r[2]),
            magnitude=ClaimMagnitude(r[3]),
            corroboration_count=int(r[4]),
            council_count=int(r[5]),
            issued_at=float(r[6]),
            previous_warrant_hash=r[7],
            self_hash=r[8],
        )
        if not warrant.verify_self_hash():
            raise WarrantChainError(
                f"Warrant {warrant.warrant_id}: self_hash mismatch — "
                f"content fields have been tampered since issue."
            )
        if warrant.previous_warrant_hash != previous_self_hash:
            raise WarrantChainError(
                f"Warrant {warrant.warrant_id}: previous_warrant_hash "
                f"({warrant.previous_warrant_hash}) does not match the "
                f"self_hash of the prior warrant ({previous_self_hash}). "
                f"Chain has been broken — either a warrant was inserted "
                f"out of order or an earlier warrant was tampered with."
            )
        previous_self_hash = warrant.self_hash


__all__ = [
    "get_warrant",
    "get_warrants_for_claim",
    "init_warrant_table",
    "issue_warrant",
    "verify_chain",
]
