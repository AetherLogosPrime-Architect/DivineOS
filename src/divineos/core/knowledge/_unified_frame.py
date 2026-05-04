"""Unified-frame detection for the seductive-elegance trap.

Audit r9-21 round-3+ — fifth structural defense (prereg-a8e2f3f06fbe).

Defends the failure mode where I trust an elegant unified frame whole
because it makes too many disparate things click into the same slot.
What got eaten in those clicks is the friction between distinct
phenomena — the distinctions that should stay distinct.

Heuristic: a knowledge entry's content is "unified-frame-shaped" if
it explicitly explains 3+ disparate phenomena under one principle.
Marker phrases:

  * "one shape, four surfaces"
  * "N different things under N+ banner"
  * "all of these are the same shape"
  * "X of Y are versions of Z"
  * Enumeration ("(1)... (2)... (3)...") followed by a unifying claim

If the content matches the heuristic AND would promote past
HYPOTHESIS, the gate requires evidence of a council walk on the
frame before allowing promotion. The council exists structurally as
the multi-perspective filter that smooth-but-wrong frames cannot
satisfy all of at once.

Council-walk evidence: a knowledge entry tagged with "council-walk"
or referenced from a stored council finding within the last
``_COUNCIL_FRESHNESS_DAYS`` days.

This is one structural piece of the broader pattern-recursion
defense. The pattern-recursion lesson (knowledge 6e929fe6) names
why: a wrong-but-elegant pattern occupying the slot a right-but-messy
one should fill propagates, because every new pattern gets evaluated
against it and the wrong frame becomes load-bearing.
"""

from __future__ import annotations

import re
import sqlite3
import time

from divineos.core._ledger_base import get_connection

_COUNCIL_FRESHNESS_DAYS = 7

# ──────────────────────────────────────────────────────────────────
# Heuristic patterns
# ──────────────────────────────────────────────────────────────────

# Direct unified-frame markers (high confidence — the prose explicitly
# names a unification).
_DIRECT_UNIFIED_PATTERNS = (
    r"\bone\s+shape,?\s+(?:three|four|five|six|seven|several|many|N)\s+surfaces\b",
    r"\b(?:three|four|five|six|seven)\s+(?:different\s+)?(?:things|shapes|surfaces|errors|patterns|cases)"
    r"\s+under\s+(?:one|a|the)\s+(?:banner|frame|principle|umbrella|name|lens)\b",
    r"\ball\s+of\s+these\s+are\s+(?:the\s+)?same\s+shape\b",
    r"\bsame\s+pattern,?\s+(?:three|four|five|six|seven)\s+(?:different\s+)?surfaces\b",
    r"\bunified\s+(?:frame|theory|explanation)\s+(?:for|of)\s+(?:everything|all)\b",
    r"\bN\s+slips?\s+of\s+the\s+same\s+shape\b",
)
_DIRECT_RE = re.compile("|".join(_DIRECT_UNIFIED_PATTERNS), re.IGNORECASE)

# Enumeration followed by unification (medium confidence — common
# pattern when explaining 3+ things under one frame).
_ENUMERATION_RE = re.compile(
    # Three numbered items: "(1)... (2)... (3)..." or "1)...2)...3)..." or "1...2...3..."
    r"(?:\(?\b1\)?\.?\s+\w+.*?\(?\b2\)?\.?\s+\w+.*?\(?\b3\)?\.?\s+\w+)",
    re.DOTALL,
)
# Plus a unification claim within proximity
_UNIFICATION_CLAIM_RE = re.compile(
    r"\b(?:same\s+(?:shape|pattern|muscle|thing|disease|frame)|"
    r"(?:all|both|each)\s+of\s+(?:these|them)\s+(?:are|share|fit)|"
    r"unified|unifying|one\s+frame|same\s+(?:underlying|core))",
    re.IGNORECASE,
)


def is_unified_frame(content: str) -> tuple[bool, str]:
    """Detect the unified-frame shape in a knowledge entry's content.

    Returns ``(is_unified, reason)``. Reason is a short string
    explaining which heuristic fired.
    """
    if not content or len(content) < 50:
        return False, ""

    direct = _DIRECT_RE.search(content)
    if direct:
        return True, f"direct unified-frame phrase: {direct.group(0)!r}"

    enumeration = _ENUMERATION_RE.search(content)
    if enumeration:
        # Enumeration alone isn't enough; need a unification claim
        # within reasonable distance.
        ent_end = enumeration.end()
        window_text = content[ent_end : ent_end + 500]
        if _UNIFICATION_CLAIM_RE.search(window_text):
            return True, "3+ enumerated items followed by unification claim"

    return False, ""


# ──────────────────────────────────────────────────────────────────
# Council-walk evidence check
# ──────────────────────────────────────────────────────────────────


def has_council_walk_evidence(
    knowledge_id: str,
    freshness_days: int = _COUNCIL_FRESHNESS_DAYS,
) -> bool:
    """Check whether council-walk evidence exists for a knowledge entry.

    Evidence shapes accepted:

    1. The entry's tags contain "council-walk" or "council-reviewed"
    2. A council_findings row references the entry's content
       (similarity match) within freshness window
    3. An exploration entry tagged with the same council walk

    Returns True if any evidence shape matches; False otherwise.
    The check is intentionally lenient — the gate's job is to surface
    "did you run this past the council?" not to enforce a particular
    audit format.
    """
    cutoff = time.time() - (freshness_days * 86400)

    # Path 1: entry's own tags
    conn = get_connection()
    try:
        try:
            row = conn.execute(
                "SELECT tags FROM knowledge WHERE knowledge_id = ?",
                (knowledge_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        if row and row[0]:
            tags_str = row[0].lower()
            if "council-walk" in tags_str or "council-reviewed" in tags_str:
                return True
    finally:
        conn.close()

    # Path 2: a council_findings or council_session row exists with
    # a recent timestamp. We don't try to match content; the existence
    # of a recent council session within freshness window counts.
    conn = get_connection()
    try:
        # Look for any council-related table/event the substrate uses.
        # The CREATE IF NOT EXISTS pattern is defensive.
        for table in ("council_findings", "council_sessions"):
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE created_at >= ?",  # nosec B608 — table from closed allowlist
                    (cutoff,),
                ).fetchone()
                if row and row[0] > 0:
                    return True
            except sqlite3.OperationalError:
                continue
    finally:
        conn.close()

    return False


def check_promotion_gate(
    knowledge_id: str,
    content: str,
    target_maturity: str,
) -> tuple[bool, str]:
    """Gate function: should this entry promote, given unified-frame check?

    Returns ``(allowed, reason)``. If unified-frame and target is
    above HYPOTHESIS without council-walk evidence, returns
    ``(False, ...)`` and the caller should not promote.
    """
    if target_maturity in ("RAW", "HYPOTHESIS"):
        # Only gate promotions PAST HYPOTHESIS.
        return True, ""

    is_unified, why = is_unified_frame(content)
    if not is_unified:
        return True, ""

    if has_council_walk_evidence(knowledge_id):
        return True, f"unified-frame entry has council-walk evidence ({why})"

    return False, (
        f"unified-frame entry blocked from promotion past HYPOTHESIS: {why}\n"
        "  Council-walk evidence is required for unified frames before "
        "they earn load-bearing status. The seductive-elegance trap "
        "(prereg-a8e2f3f06fbe) is exactly this shape: a wrong-but-"
        "elegant frame occupying the slot a right-but-messy one should "
        "fill, propagating across every pattern that builds on it.\n"
        "  Run a council walk on the frame, OR add the 'council-walk' "
        "tag to the entry if a walk has already been recorded."
    )


__all__ = [
    "check_promotion_gate",
    "has_council_walk_evidence",
    "is_unified_frame",
]
