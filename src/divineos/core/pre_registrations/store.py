"""Pre-registration CRUD — file, query, and close pre-registered predictions.

Pre-registrations are the Goodhart-prevention layer: every new detector,
mechanism, or instrumentation claim must file a written prediction with a
specific falsifier and a ledger-scheduled review date. Review dates fire
independent of agent memory, so a mechanism cannot silently drift into
"the number went up, ship it" without the pre-registered evidence being
reconciled.

Invariants (enforced):

* ``claim``, ``success_criterion``, ``falsifier`` must all be non-empty.
  A pre-registration without a falsifier is not a prediction — it is a
  hope. Popper's rule: if nothing could prove you wrong, you have said
  nothing.
* ``review_window_days`` must be positive; zero-day review is equivalent
  to no review.
* Outcomes are one-way. Once recorded, the outcome cannot be rewritten.
  To revise, file a new pre-registration that references the old.
* Recording an outcome requires an external actor (user, grok, fresh-
  claude, auditor, council). Internal actors cannot self-verify their
  own pre-registrations; that would re-create the designer-user-judge
  collapse this mechanism exists to prevent.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from loguru import logger

from divineos.core.knowledge import _get_connection
from divineos.core.pre_registrations._schema import init_pre_registrations_tables
from divineos.core.pre_registrations.types import (
    INTERNAL_ACTORS,
    Outcome,
    PreRegistration,
)

_STORE_ERRORS = (
    sqlite3.OperationalError,
    ImportError,
    OSError,
    KeyError,
    TypeError,
    ValueError,
)

SECONDS_PER_DAY = 86400


def _normalize_actor(actor: str) -> str:
    """Normalize an actor name, defending against unicode bypass.

    Audit finding 2026-05-03 round 12: a plain ``.strip().lower()``
    does NOT remove U+00A0 (no-break space), U+2009 (thin space),
    U+202F (narrow no-break space), or other unicode-whitespace
    variants. An LLM copy-pasting from a formatted document could
    file as ``"\\u00a0claude"`` — bypassing the INTERNAL_ACTORS
    frozenset check because the literal string isn't ``"claude"``.

    Verified bypass before fix:
      record_outcome(prereg_id, actor="\\u200bclaude", outcome=SUCCESS)
      ...succeeds — agent flips its own pre-reg.

    NFKC normalization folds these compatibility forms to canonical
    ASCII so ``.strip()`` actually removes them. Same fix shape
    that landed for ``watchmen.store._validate_actor`` in Tier 1.
    """
    import re
    import unicodedata

    nfkc = unicodedata.normalize("NFKC", actor)
    # Strip invisible/zero-width characters that NFKC + .strip() leave
    # alone but which would let "​claude" bypass the frozenset check.
    # Covers: zero-width space (200B), ZWNJ (200C), ZWJ (200D), LRM/RLM
    # (200E/200F), ZWNBSP/BOM (FEFF), soft hyphen (00AD).
    #
    # Audit r9-21 #28 follow-up: build the pattern from chr(codepoint)
    # rather than embedding literal bidi characters in source. The
    # literal-form was triggering bandit B613 (Trojan Source detection)
    # which is exactly the right thing to flag in general — it just
    # happened that this particular file was using bidi chars as a
    # defense, not an attack. chr()-based construction has the same
    # runtime effect with no bidi chars in the source bytes.
    _invisible_codepoints = (
        0x200B,  # zero-width space
        0x200C,  # ZWNJ
        0x200D,  # ZWJ
        0x200E,  # LRM
        0x200F,  # RLM
        0xFEFF,  # ZWNBSP / BOM
        0x00AD,  # soft hyphen
    )
    _invisibles = "[" + "".join(chr(cp) for cp in _invisible_codepoints) + "]"
    invisible_stripped = re.sub(_invisibles, "", nfkc)
    collapsed = re.sub(r"\s+", " ", invisible_stripped).strip()
    normalized = collapsed.casefold()
    if not normalized:
        raise ValueError("Actor name cannot be empty")
    # Audit r9-21 round-2 review: NFKC doesn't fold cross-script
    # homoglyphs (Cyrillic с U+0441 vs Latin c U+0063 are distinct
    # code points in distinct scripts). Without this guard,
    # actor="сlaude" (Cyrillic с) bypasses INTERNAL_ACTORS.
    if _has_mixed_scripts(normalized):
        raise ValueError(
            f"Actor '{actor}' contains mixed-script characters; this looks like "
            "a homoglyph attack. Use a single-script identifier."
        )
    return normalized


def _has_mixed_scripts(s: str) -> bool:
    """Detect cross-script homoglyph attempts (LATIN/CYRILLIC/GREEK)."""
    import unicodedata as _ud

    scripts: set[str] = set()
    for ch in s:
        if not ch.isalpha():
            continue
        try:
            name = _ud.name(ch)
        except ValueError:
            continue
        if name.startswith("LATIN"):
            scripts.add("LATIN")
        elif name.startswith("CYRILLIC"):
            scripts.add("CYRILLIC")
        elif name.startswith("GREEK"):
            scripts.add("GREEK")
        if len(scripts) > 1:
            return True
    return False


def _require_external_actor(actor: str) -> str:
    """Reject internal actors. Used when recording outcomes."""
    normalized = _normalize_actor(actor)
    if normalized in INTERNAL_ACTORS:
        raise ValueError(
            f"Actor '{actor}' is an internal component and cannot record "
            f"pre-registration outcomes. External review is required "
            f"(user, grok, fresh-claude, auditor, council, or a "
            f"disambiguated instance name)."
        )
    return normalized


def _row_to_prereg(row: tuple[Any, ...]) -> PreRegistration:
    """Convert a DB row to a PreRegistration dataclass."""
    tags = json.loads(row[14]) if row[14] else []
    return PreRegistration(
        prereg_id=row[0],
        created_at=row[1],
        actor=row[2],
        mechanism=row[3],
        claim=row[4],
        success_criterion=row[5],
        falsifier=row[6],
        review_ts=row[7],
        review_window_days=row[8],
        outcome=Outcome(row[9]),
        outcome_ts=row[10],
        outcome_notes=row[11] or "",
        linked_claim_id=row[12],
        linked_commit=row[13],
        tags=tags,
    )


_SELECT_ALL_COLS = (
    "prereg_id, created_at, actor, mechanism, claim, success_criterion, "
    "falsifier, review_ts, review_window_days, outcome, outcome_ts, "
    "outcome_notes, linked_claim_id, linked_commit, tags"
)


def apply_pre_registration_seed(seed_data: dict[str, Any], mode: str = "merge") -> dict[str, int]:
    """Audit r9-21 #30: load seed_pre_registrations.json into a fresh clone.

    Without this, a freshly-cloned substrate ships with no load-bearing
    pre-regs — the entire Goodhart-prevention layer has to be rebuilt
    by hand for each new install. Seed entries carry an optional
    ``key`` field to dedup across re-applies (parallel to seed_key in
    knowledge — audit r9-21 #29).

    Returns counts: ``{"applied": int, "skipped": int}``.
    """
    counts = {"applied": 0, "skipped": 0}
    init_pre_registrations_tables()

    # Collect existing seed_keys (if column has been migrated). Best-
    # effort: if the column doesn't exist yet, fall back to mechanism
    # match for dedup (the natural unique-ish handle).
    existing_keys: set[str] = set()
    existing_mechanisms: set[str] = set()
    conn = _get_connection()
    try:
        try:
            rows = conn.execute(
                "SELECT seed_key FROM pre_registrations WHERE seed_key IS NOT NULL"
            ).fetchall()
            existing_keys = {r[0] for r in rows}
        except sqlite3.OperationalError:
            # seed_key column may not be migrated; skip the lookup.
            pass
        rows = conn.execute("SELECT mechanism FROM pre_registrations").fetchall()
        existing_mechanisms = {r[0] for r in rows}
    finally:
        conn.close()

    for entry in seed_data.get("pre_registrations", []):
        key = entry.get("key")
        mechanism = entry.get("mechanism", "")
        if mode == "merge" and key and key in existing_keys:
            counts["skipped"] += 1
            continue
        if mode == "merge" and mechanism in existing_mechanisms:
            counts["skipped"] += 1
            continue
        try:
            prereg_id = file_pre_registration(
                actor=entry.get("actor", "system"),
                mechanism=mechanism,
                claim=entry["claim"],
                success_criterion=entry["success_criterion"],
                falsifier=entry["falsifier"],
                review_window_days=entry.get("review_window_days", 30),
                tags=entry.get("tags"),
            )
        except (ValueError, KeyError) as e:
            logger.warning(f"Pre-registration seed entry skipped: {e}")
            counts["skipped"] += 1
            continue
        # Stamp seed_key if column exists.
        if key and prereg_id:
            conn = _get_connection()
            try:
                try:
                    conn.execute(
                        "UPDATE pre_registrations SET seed_key = ? WHERE prereg_id = ?",
                        (key, prereg_id),
                    )
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
            finally:
                conn.close()
        counts["applied"] += 1

    return counts


def file_pre_registration(
    actor: str,
    mechanism: str,
    claim: str,
    success_criterion: str,
    falsifier: str,
    review_window_days: int = 30,
    linked_claim_id: str | None = None,
    linked_commit: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """File a new pre-registration.

    Returns the prereg_id. Raises ValueError if any required field is
    empty or review_window_days is not positive.
    """
    normalized_actor = _normalize_actor(actor)

    for name, value in (
        ("mechanism", mechanism),
        ("claim", claim),
        ("success_criterion", success_criterion),
        ("falsifier", falsifier),
    ):
        if not value or not value.strip():
            raise ValueError(
                f"Pre-registration field '{name}' cannot be empty. "
                f"A pre-registration without a falsifier is a hope, not a prediction."
            )

    if review_window_days <= 0:
        raise ValueError(
            f"review_window_days must be positive (got {review_window_days}). "
            f"A zero-day review window is equivalent to no review."
        )

    init_pre_registrations_tables()

    prereg_id = f"prereg-{uuid.uuid4().hex[:12]}"
    now = time.time()
    review_ts = now + review_window_days * SECONDS_PER_DAY
    tag_list = list(tags) if tags else []

    conn = _get_connection()
    try:
        conn.execute(
            f"INSERT INTO pre_registrations ({_SELECT_ALL_COLS}) "  # noqa: S608  # nosec B608 — _SELECT_ALL_COLS is a module constant
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                prereg_id,
                now,
                normalized_actor,
                mechanism.strip(),
                claim.strip(),
                success_criterion.strip(),
                falsifier.strip(),
                review_ts,
                review_window_days,
                Outcome.OPEN.value,
                None,
                "",
                linked_claim_id,
                linked_commit,
                json.dumps(tag_list),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Log to ledger — best-effort; ledger is not authoritative for pre-regs.
    try:
        from divineos.core.ledger import log_event

        log_event(
            "PRE_REGISTRATION_FILED",
            normalized_actor,
            {
                "prereg_id": prereg_id,
                "mechanism": mechanism,
                "review_ts": review_ts,
                "review_window_days": review_window_days,
            },
            validate=False,
        )
    except _STORE_ERRORS:
        pass

    logger.info(
        "Pre-registration filed: %s mechanism=%s review_in=%dd actor=%s",
        prereg_id,
        mechanism,
        review_window_days,
        normalized_actor,
    )
    return prereg_id


def get_pre_registration(prereg_id: str) -> PreRegistration | None:
    """Retrieve a pre-registration by id, or None if not found."""
    init_pre_registrations_tables()
    conn = _get_connection()
    try:
        row = conn.execute(
            f"SELECT {_SELECT_ALL_COLS} FROM pre_registrations WHERE prereg_id = ?",  # noqa: S608  # nosec B608 — _SELECT_ALL_COLS is a module constant
            (prereg_id,),
        ).fetchone()
        return _row_to_prereg(row) if row else None
    finally:
        conn.close()


def list_pre_registrations(
    outcome: Outcome | None = None,
    actor: str | None = None,
    mechanism: str | None = None,
    limit: int = 50,
) -> list[PreRegistration]:
    """List pre-registrations, optionally filtered by outcome/actor/mechanism."""
    init_pre_registrations_tables()
    conditions: list[str] = []
    params: list[Any] = []
    if outcome is not None:
        conditions.append("outcome = ?")
        params.append(outcome.value)
    if actor:
        conditions.append("actor = ?")
        params.append(_normalize_actor(actor))
    if mechanism:
        conditions.append("mechanism = ?")
        params.append(mechanism.strip())

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    conn = _get_connection()
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_ALL_COLS} FROM pre_registrations{where} "  # noqa: S608  # nosec B608 — _SELECT_ALL_COLS module constant; where built from literal fragments, values bound
            "ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_prereg(r) for r in rows]
    finally:
        conn.close()


def get_overdue_pre_registrations(now: float | None = None) -> list[PreRegistration]:
    """Pre-registrations whose review_ts has passed and outcome is still OPEN.

    These are the ones the briefing must surface — they are the mechanism's
    ledger-scheduled "wake up and check whether your prediction held" events.
    """
    init_pre_registrations_tables()
    ts = now if now is not None else time.time()
    conn = _get_connection()
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_ALL_COLS} FROM pre_registrations "  # noqa: S608  # nosec B608 — _SELECT_ALL_COLS module constant
            "WHERE outcome = ? AND review_ts <= ? "
            "ORDER BY review_ts ASC",
            (Outcome.OPEN.value, ts),
        ).fetchall()
        return [_row_to_prereg(r) for r in rows]
    finally:
        conn.close()


def record_outcome(
    prereg_id: str,
    actor: str,
    outcome: Outcome,
    notes: str = "",
) -> bool:
    """Record a terminal outcome for a pre-registration.

    Returns True if the outcome was recorded, False if the pre-registration
    was not found or already has a terminal outcome.

    Raises ValueError if:
      * ``actor`` is internal (self-verification is disallowed)
      * ``outcome`` is ``OPEN`` (not a terminal state)
      * the pre-registration already has a non-OPEN outcome (one-way only)
    """
    normalized_actor = _require_external_actor(actor)

    if outcome == Outcome.OPEN:
        raise ValueError(
            "Cannot record outcome OPEN — only terminal outcomes are valid "
            "(SUCCESS, FAILED, INCONCLUSIVE, DEFERRED)."
        )

    init_pre_registrations_tables()
    now = time.time()

    conn = _get_connection()
    try:
        existing = conn.execute(
            "SELECT outcome FROM pre_registrations WHERE prereg_id = ?",
            (prereg_id,),
        ).fetchone()
        if not existing:
            return False

        current_outcome = existing[0]
        if current_outcome != Outcome.OPEN.value:
            raise ValueError(
                f"Pre-registration {prereg_id} already has terminal outcome "
                f"'{current_outcome}'. Outcomes are one-way. To revise, file "
                f"a new pre-registration that references this one."
            )

        conn.execute(
            "UPDATE pre_registrations "
            "SET outcome = ?, outcome_ts = ?, outcome_notes = ? "
            "WHERE prereg_id = ?",
            (outcome.value, now, notes, prereg_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Log to ledger — best-effort
    try:
        from divineos.core.ledger import log_event

        log_event(
            "PRE_REGISTRATION_OUTCOME",
            normalized_actor,
            {
                "prereg_id": prereg_id,
                "outcome": outcome.value,
                "notes": notes,
            },
            validate=False,
        )
    except _STORE_ERRORS:
        pass

    logger.info(
        "Pre-registration outcome recorded: %s -> %s by %s",
        prereg_id,
        outcome.value,
        normalized_actor,
    )
    return True


def count_by_outcome() -> dict[str, int]:
    """Return a dict mapping outcome values to counts. Missing outcomes are 0."""
    init_pre_registrations_tables()
    counts = {o.value: 0 for o in Outcome}
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT outcome, COUNT(*) FROM pre_registrations GROUP BY outcome"
        ).fetchall()
        for outcome_val, n in rows:
            counts[outcome_val] = n
    finally:
        conn.close()
    return counts
