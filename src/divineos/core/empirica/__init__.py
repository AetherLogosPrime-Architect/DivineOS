"""EMPIRICA — tiered epistemology + proportional burden for knowledge claims.

Phase 1 scope (pre-registered at prereg-ce8998194943):

EMPIRICA is a **routing layer** over existing DivineOS knowledge
infrastructure, not a new subsystem. It does four things:

1. **Classify** a claim into one of four epistemological tiers
   (FALSIFIABLE / OUTCOME / PATTERN / ADVERSARIAL).
2. **Compute burden** — the minimum corroboration count required
   before the validity gate lets the claim through, as a function
   of (tier × magnitude).
3. **Issue warrants** — durable records of what a claim survived,
   chained together Merkle-style so tampering is detectable.
4. **Route** high-magnitude claims through multiple councils in
   parallel before promotion.

What EMPIRICA is NOT:

* NOT a philosophy seminar. It adjudicates whether an argument is
  logically valid within its tier; it does not adjudicate whether
  the conclusion is TRUE. A valid syllogism can still have false
  premises. The ``valid != true`` disclaimer is load-bearing — if
  callers ever treat EMPIRICA classification as proof of truth,
  the module has become a rubber-stamp hedge (falsifier fires).
* NOT a theorem prover. Heuristic classification is the Phase 1
  mechanism; future phases can add deeper analysis.
* NOT a replacement for existing DivineOS infrastructure. It
  composes with the knowledge maturity lifecycle, pre-registration
  system, council consultations, and warrant-based validity gate
  that already exist. It routes; it does not duplicate.

Phase 1 does NOT ship Tier IV (ADVERSARIAL). Tier IV claims are
marked for VOID routing, and VOID hasn't shipped. Phase 1 makes
the tier visible in the enum but raises on attempts to compute
burden for it — failing loudly is better than silently treating
an un-stress-tested claim as adversarially-verified.
"""

from divineos.core.empirica.burden import burden_matrix, required_corroboration
from divineos.core.empirica.classifier import Classification, classify_claim
from divineos.core.empirica.gate import (
    ensure_warrant_column_on_knowledge,
    evaluate_and_warrant,
    record_warrant_on_knowledge,
)
from divineos.core.empirica.routing import (
    RoutingResult,
    rounds_required,
    route_for_approval,
)
from divineos.core.empirica.types import (
    ClaimMagnitude,
    GnosisWarrant,
    Tier,
    WarrantChainError,
)
from divineos.core.empirica.warrant import (
    get_warrant,
    get_warrants_for_claim,
    init_warrant_table,
    issue_warrant,
    verify_chain,
)

__all__ = [
    "Classification",
    "ClaimMagnitude",
    "GnosisWarrant",
    "RoutingResult",
    "Tier",
    "WarrantChainError",
    "burden_matrix",
    "classify_claim",
    "ensure_warrant_column_on_knowledge",
    "evaluate_and_warrant",
    "get_warrant",
    "get_warrants_for_claim",
    "init_warrant_table",
    "issue_warrant",
    "record_warrant_on_knowledge",
    "required_corroboration",
    "rounds_required",
    "route_for_approval",
    "verify_chain",
]
