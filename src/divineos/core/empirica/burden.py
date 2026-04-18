"""Proportional burden — (tier × magnitude) → required corroboration.

The sharpest idea in Andrew's original EMPIRICA spec, restated in plain
code: the evidence threshold a claim must cross is a function of both
what KIND of claim it is (tier) and how LOAD-BEARING it is (magnitude).

A falsifiable claim about a CLI truncation bug (TRIVIAL magnitude) needs
less evidence than a pattern claim about cross-session recurrence
(LOAD_BEARING magnitude), even though both use the validity gate.

## The formula

    required_corroboration(tier, magnitude) = BASE[tier] * (1 + magnitude.value)

BASE values (hand-picked; will be tuned based on pre-reg review data):

* ``FALSIFIABLE`` base = 2 — a repeatable test needs independent repro
* ``OUTCOME`` base = 3 — mechanism-opaque claims need more outcomes
* ``PATTERN`` base = 4 — pattern claims need more instances to rule out coincidence
* ``ADVERSARIAL`` base = N/A — Phase 1 raises NotImplementedError

Magnitudes multiply: TRIVIAL=1×, NORMAL=2×, LOAD_BEARING=3×, FOUNDATIONAL=4×.

Worked examples:

* Tier I FALSIFIABLE + TRIVIAL = 2×1 = **2** corroborations
* Tier I FALSIFIABLE + NORMAL = 2×2 = **4**
* Tier III PATTERN + NORMAL = 4×2 = **8**
* Tier III PATTERN + FOUNDATIONAL = 4×4 = **16**
* Tier III PATTERN + TRIVIAL = 4×1 = **4** (same as FALSIFIABLE NORMAL —
  pattern is inherently more demanding than falsifiable at equal
  magnitude, which reflects the epistemological reality)

## Why these numbers, not others

The pre-reg (prereg-ce8998194943) makes the tuning falsifiable: if
after 30 days of real-world use, Tier I vs Tier III claims produce
the same empirical rejection rate, the numbers are wrong and the
calculator is decorative. Calibration happens based on evidence,
not on vibes. Ship with reasonable defaults; tune when the data says.

## What this module is NOT

Not a policy enforcer — just a number. Deciding what to do with the
number is the validity gate's job (route the claim to councils, promote
to warrant-issue, reject). Burden is a scalar; consequence is another
module's call. Keeps the calculator small and easy to audit.
"""

from __future__ import annotations

from divineos.core.empirica.types import ClaimMagnitude, Tier


# Base corroboration counts per tier. These are the starting points —
# magnitude multiplies on top. Phase 1 values; tuning is a pre-reg
# review artifact at 30 days.
_TIER_BASE_CORROBORATION: dict[Tier, int] = {
    Tier.FALSIFIABLE: 2,  # repeatable test → needs independent repro
    Tier.OUTCOME: 3,  # mechanism-opaque → more observations
    Tier.PATTERN: 4,  # coincidence-rule-out → more instances
    # Tier.ADVERSARIAL intentionally absent — handled by explicit raise
    # in required_corroboration() rather than silent default. Failing
    # loudly beats silent fallback for an unimplemented tier.
}


def required_corroboration(tier: Tier, magnitude: ClaimMagnitude) -> int:
    """Compute the minimum corroboration needed for a claim.

    Raises ``NotImplementedError`` for ``Tier.ADVERSARIAL`` — Phase 1
    does not implement adversarial burden (that requires the VOID
    module, not yet shipped). Until VOID exists, a caller asking for
    ADVERSARIAL burden is asking for something the system can't honestly
    provide; the honest answer is to fail loudly rather than return a
    number that pretends the stress-test happened.

    The multiplier ``(1 + magnitude.value)`` means TRIVIAL (value=0)
    gives the base unmodified, and each magnitude step adds one full
    base's worth of required corroboration. FOUNDATIONAL claims need
    4x the base of the same tier — the architecture is built on them,
    so mistakes propagate and the threshold should reflect that.
    """
    if tier is Tier.ADVERSARIAL:
        raise NotImplementedError(
            "Tier.ADVERSARIAL burden requires the VOID module "
            "(adversarial sandbox / steelman sparring), which has "
            "not shipped. Phase 1 EMPIRICA deliberately fails loudly "
            "rather than return a number that would misrepresent "
            "un-stress-tested claims as adversarially-verified. "
            "See prereg-ce8998194943 for the scoping."
        )
    base = _TIER_BASE_CORROBORATION[tier]
    return base * (1 + magnitude.value)


def burden_matrix() -> dict[tuple[Tier, ClaimMagnitude], int]:
    """Return the full (tier, magnitude) → corroboration matrix.

    Useful for documentation, UI display, and for tests verifying that
    the calculator produces measurably different values across tiers
    at equal magnitudes (pre-reg falsifier #2 — if this matrix collapses
    to a single value, the calculator is decorative).
    """
    matrix: dict[tuple[Tier, ClaimMagnitude], int] = {}
    for tier in (Tier.FALSIFIABLE, Tier.OUTCOME, Tier.PATTERN):
        for magnitude in ClaimMagnitude:
            matrix[(tier, magnitude)] = required_corroboration(tier, magnitude)
    return matrix


__all__ = [
    "burden_matrix",
    "required_corroboration",
]
