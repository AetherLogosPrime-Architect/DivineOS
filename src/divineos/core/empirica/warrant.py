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
    WarrantForkError,
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
    """Walk the warrant chain by HASH POINTERS and verify integrity.

    Traversal is a forest walk starting from genesis warrants (those
    with ``previous_warrant_hash = None``). We do NOT sort by
    ``issued_at`` — that was the bug Dijkstra flagged in audit
    finding find-0ea12ad4b5d0. In a Merkle chain, hashes define
    order; order does not define which hashes must match. Reversing
    that dependency turned honest concurrent-writer races into
    tamper-shaped false positives.

    Invariants checked (in order):

    1. Every warrant's ``self_hash`` recomputes from its current
       field values (no content tampering).
    2. Every ``self_hash`` is unique in the store.
    3. Exactly one genesis warrant exists. Two or more is a fork
       at the root — usually concurrent writers on an empty store.
    4. Walking from genesis following ``self_hash →
       previous_warrant_hash`` edges, every node has exactly one
       child. Two or more children is a fork at that position —
       usually concurrent writers racing on the same latest-warrant
       snapshot. Zero children is end-of-chain.
    5. The traversal reaches every warrant in the store. Any
       warrant not reached is orphaned — its
       ``previous_warrant_hash`` points to a hash that doesn't
       exist in the store (stale or tampered reference).
    6. No cycles — a warrant whose traversal revisits a hash
       already in the chain.

    Raises:

    * ``WarrantForkError`` (subclass of ``WarrantChainError``)
      when invariants 3 or 4 fail — distinguishes honest
      concurrent-writer forks from tamper events so operators
      can diagnose correctly.
    * ``WarrantChainError`` when invariants 1, 2, 5, or 6 fail.

    Silent on success.
    """
    init_warrant_table()
    conn = _get_ledger_conn()
    try:
        rows = conn.execute(
            "SELECT warrant_id, claim_id, tier, magnitude, corroboration_count, "
            "council_count, issued_at, previous_warrant_hash, self_hash "
            "FROM gnosis_warrants"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return  # empty store is valid

    # Invariants 1 + 2: verify every warrant's self_hash, build
    # by_self_hash index, build children_of adjacency.
    by_self_hash: dict[str, GnosisWarrant] = {}
    children_of: dict[str | None, list[GnosisWarrant]] = {}
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
        if warrant.self_hash in by_self_hash:
            prior = by_self_hash[warrant.self_hash]
            raise WarrantChainError(
                f"Duplicate self_hash {warrant.self_hash[:16]}... appears "
                f"on warrants {prior.warrant_id} and {warrant.warrant_id}. "
                f"Hash collisions should not occur for this field set."
            )
        by_self_hash[warrant.self_hash] = warrant
        children_of.setdefault(warrant.previous_warrant_hash, []).append(warrant)

    # Invariant 3: exactly one genesis warrant.
    genesis = children_of.get(None, [])
    if not genesis:
        raise WarrantChainError(
            "No genesis warrant found (every warrant has a "
            "previous_warrant_hash). Chain has no root — all previous "
            "links must eventually terminate at a genesis warrant."
        )
    if len(genesis) > 1:
        ids = ", ".join(w.warrant_id for w in genesis)
        raise WarrantForkError(
            f"Multiple genesis warrants found ({len(genesis)}): {ids}. "
            f"Chain has forked at the root — likely concurrent writers "
            f"on an empty store. Each genesis warrant is valid on its "
            f"own; the fork needs operator intervention to pick the "
            f"canonical history."
        )

    # Invariant 4 + 6: walk forward from genesis, one child per node,
    # no cycles.
    current = genesis[0]
    visited: set[str] = {current.self_hash}
    while True:
        kids = children_of.get(current.self_hash, [])
        if len(kids) == 0:
            break
        if len(kids) > 1:
            ids = ", ".join(w.warrant_id for w in kids)
            raise WarrantForkError(
                f"Fork at warrant {current.warrant_id} "
                f"({current.self_hash[:16]}...): {len(kids)} warrants "
                f"chain to its self_hash: {ids}. Likely concurrent "
                f"writers racing on the same latest-warrant snapshot. "
                f"Investigate the race and consider adding a write-side "
                f"lock or compare-and-swap on the previous hash."
            )
        current = kids[0]
        if current.self_hash in visited:
            raise WarrantChainError(
                f"Cycle detected at warrant {current.warrant_id}: "
                f"traversal revisits self_hash already in the chain. "
                f"This should be structurally impossible unless a "
                f"warrant was manually inserted with a crafted hash."
            )
        visited.add(current.self_hash)

    # Invariant 5: every warrant reached from the genesis walk.
    unvisited = set(by_self_hash.keys()) - visited
    if unvisited:
        orphans = sorted(by_self_hash[h].warrant_id for h in unvisited)
        sample = ", ".join(orphans[:5])
        suffix = f" (and {len(orphans) - 5} more)" if len(orphans) > 5 else ""
        raise WarrantChainError(
            f"Orphan warrants not reached from genesis traversal: "
            f"{sample}{suffix}. Their previous_warrant_hash references "
            f"point to self_hashes that do not exist in the store — "
            f"either stale (a prior warrant was deleted) or tampered "
            f"(a previous_warrant_hash was edited to a fake value)."
        )


__all__ = [
    "get_warrant",
    "get_warrants_for_claim",
    "init_warrant_table",
    "issue_warrant",
    "verify_chain",
]
