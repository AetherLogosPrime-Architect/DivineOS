"""Tests for EMPIRICA Phase 1 (prereg-ce8998194943).

Coverage:

* ``types`` — Tier/ClaimMagnitude enum invariants, GnosisWarrant hash
  + chain-link correctness + tamper detection.
* ``burden`` — required_corroboration returns measurably different
  values across tiers at equal magnitudes (pre-reg falsifier #2);
  ADVERSARIAL raises; matrix shape correct.
* ``classifier`` — each rule fires on its canonical trigger; default
  fallback path is labeled; magnitude heuristics hit their keywords.
* ``warrant`` — persistence round-trip; chain verification catches
  in-DB tampering; per-claim retrieval ordering.
* ``routing`` — TRIVIAL/NORMAL skip council; LOAD_BEARING requires 1
  round; FOUNDATIONAL requires 2; any blocked round rejects.
* ``gate`` — full pipeline: burden rejection, council rejection,
  pass-through with warrant; warrant_id column migration.
* Cross-module invariants — valid != true disclaimer is discoverable;
  Tier.ADVERSARIAL propagates rejection through all layers.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from divineos.core.empirica.burden import burden_matrix, required_corroboration
from divineos.core.empirica.classifier import classify_claim
from divineos.core.empirica.gate import (
    ensure_warrant_column_on_knowledge,
    evaluate_and_warrant,
    record_warrant_on_knowledge,
)
from divineos.core.empirica.routing import (
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


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    os.environ["DIVINEOS_DB"] = str(tmp_path / "empirica-test.db")
    try:
        from divineos.core.knowledge import init_knowledge_table
        from divineos.core.ledger import init_db

        init_db()
        init_knowledge_table()
        init_warrant_table()
        yield
    finally:
        os.environ.pop("DIVINEOS_DB", None)


# ── types ────────────────────────────────────────────────────────────


class TestTierEnum:
    def test_four_tiers_defined(self):
        assert len(list(Tier)) == 4

    def test_tiers_serialize_as_strings(self):
        assert Tier.FALSIFIABLE.value == "falsifiable"
        assert Tier.OUTCOME.value == "outcome"
        assert Tier.PATTERN.value == "pattern"
        assert Tier.ADVERSARIAL.value == "adversarial"

    def test_tier_is_str_subclass(self):
        """String-subclass so SQLite + JSON serialize cleanly."""
        assert isinstance(Tier.FALSIFIABLE, str)


class TestClaimMagnitudeEnum:
    def test_four_magnitudes(self):
        assert len(list(ClaimMagnitude)) == 4

    def test_values_are_integers_ordered(self):
        """Integer values are the burden multiplier — must be ordered."""
        assert ClaimMagnitude.TRIVIAL.value == 0
        assert ClaimMagnitude.NORMAL.value == 1
        assert ClaimMagnitude.LOAD_BEARING.value == 2
        assert ClaimMagnitude.FOUNDATIONAL.value == 3


class TestGnosisWarrantSelfHash:
    def test_issue_computes_self_hash(self):
        w = GnosisWarrant.issue(
            claim_id="c1",
            tier=Tier.FALSIFIABLE,
            magnitude=ClaimMagnitude.NORMAL,
            corroboration_count=4,
            council_count=0,
            previous_warrant_hash=None,
        )
        assert w.self_hash
        assert len(w.self_hash) == 64  # sha256 hex

    def test_verify_self_hash_true_on_fresh(self):
        w = GnosisWarrant.issue("c1", Tier.PATTERN, ClaimMagnitude.LOAD_BEARING, 12, 1, None)
        assert w.verify_self_hash() is True

    def test_verify_self_hash_false_after_tamper(self):
        """Using dataclass.replace to tamper with a frozen field."""
        w = GnosisWarrant.issue("c1", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4, 0, None)
        tampered = replace(w, corroboration_count=999)
        assert tampered.verify_self_hash() is False

    def test_self_hash_deterministic_across_issues_with_same_inputs(self):
        """Two warrants with identical content produce identical hashes.

        Since issued_at is wall-clock, use the internal compute method
        directly with a fixed timestamp."""
        h1 = GnosisWarrant._compute_self_hash(
            "c", Tier.OUTCOME, ClaimMagnitude.NORMAL, 6, 0, 1000.0, None
        )
        h2 = GnosisWarrant._compute_self_hash(
            "c", Tier.OUTCOME, ClaimMagnitude.NORMAL, 6, 0, 1000.0, None
        )
        assert h1 == h2

    def test_self_hash_changes_with_tier(self):
        h1 = GnosisWarrant._compute_self_hash(
            "c", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4, 0, 1000.0, None
        )
        h2 = GnosisWarrant._compute_self_hash(
            "c", Tier.PATTERN, ClaimMagnitude.NORMAL, 4, 0, 1000.0, None
        )
        assert h1 != h2


# ── burden ───────────────────────────────────────────────────────────


class TestRequiredCorroboration:
    def test_falsifiable_trivial(self):
        assert required_corroboration(Tier.FALSIFIABLE, ClaimMagnitude.TRIVIAL) == 2

    def test_falsifiable_normal(self):
        assert required_corroboration(Tier.FALSIFIABLE, ClaimMagnitude.NORMAL) == 4

    def test_falsifiable_foundational(self):
        assert required_corroboration(Tier.FALSIFIABLE, ClaimMagnitude.FOUNDATIONAL) == 8

    def test_outcome_normal(self):
        assert required_corroboration(Tier.OUTCOME, ClaimMagnitude.NORMAL) == 6

    def test_pattern_foundational_is_highest(self):
        assert required_corroboration(Tier.PATTERN, ClaimMagnitude.FOUNDATIONAL) == 16

    def test_adversarial_raises_not_implemented(self):
        """Tier IV intentionally unimplemented in Phase 1 — waits for VOID."""
        with pytest.raises(NotImplementedError, match="VOID"):
            required_corroboration(Tier.ADVERSARIAL, ClaimMagnitude.NORMAL)

    def test_tiers_differ_at_equal_magnitude(self):
        """Pre-reg falsifier #2: if the calculator produces the same
        value across tiers at equal magnitude, it's decorative."""
        f = required_corroboration(Tier.FALSIFIABLE, ClaimMagnitude.NORMAL)
        o = required_corroboration(Tier.OUTCOME, ClaimMagnitude.NORMAL)
        p = required_corroboration(Tier.PATTERN, ClaimMagnitude.NORMAL)
        assert f != o and o != p and f != p

    def test_magnitude_scales_within_tier(self):
        """Within a tier, higher magnitude requires strictly more."""
        vals = [
            required_corroboration(Tier.PATTERN, m)
            for m in (
                ClaimMagnitude.TRIVIAL,
                ClaimMagnitude.NORMAL,
                ClaimMagnitude.LOAD_BEARING,
                ClaimMagnitude.FOUNDATIONAL,
            )
        ]
        assert vals == sorted(vals)
        assert len(set(vals)) == 4  # all distinct


class TestBurdenMatrix:
    def test_matrix_covers_three_tiers(self):
        """ADVERSARIAL deliberately omitted (raises on required_corroboration)."""
        m = burden_matrix()
        tiers_present = {t for (t, _) in m}
        assert tiers_present == {Tier.FALSIFIABLE, Tier.OUTCOME, Tier.PATTERN}

    def test_matrix_covers_four_magnitudes(self):
        m = burden_matrix()
        mags = {mag for (_, mag) in m}
        assert mags == set(ClaimMagnitude)

    def test_matrix_twelve_entries(self):
        assert len(burden_matrix()) == 12


# ── classifier ───────────────────────────────────────────────────────


class TestClassifierRules:
    def test_pattern_knowledge_type_routes_to_pattern(self):
        c = classify_claim("content", knowledge_type="PATTERN")
        assert c.tier == Tier.PATTERN
        assert "rule-1" in c.reason

    def test_fact_measured_routes_to_falsifiable(self):
        c = classify_claim("x", knowledge_type="FACT", source="measured")
        assert c.tier == Tier.FALSIFIABLE
        assert "rule-2" in c.reason

    def test_outcome_types_route_to_outcome(self):
        for kt in ("PRINCIPLE", "BOUNDARY", "MISTAKE", "DIRECTIVE"):
            c = classify_claim("x", knowledge_type=kt)
            assert c.tier == Tier.OUTCOME, f"knowledge_type={kt}"
            assert "rule-3" in c.reason

    def test_pattern_keyword_triggers_pattern(self):
        c = classify_claim("pattern recurring across multiple sessions")
        assert c.tier == Tier.PATTERN
        assert "rule-4" in c.reason

    def test_falsifiability_keyword_triggers_falsifiable(self):
        c = classify_claim("threshold asserts repeatable behavior")
        assert c.tier == Tier.FALSIFIABLE
        assert "rule-5" in c.reason

    def test_default_to_outcome(self):
        c = classify_claim("some content with no signal at all")
        assert c.tier == Tier.OUTCOME
        assert "rule-6" in c.reason

    def test_classifier_never_returns_adversarial(self):
        """ADVERSARIAL requires VOID routing — never auto-assigned."""
        for content in ("adversarial attack", "red team survival", "steelman claim"):
            c = classify_claim(content)
            assert c.tier != Tier.ADVERSARIAL

    def test_reason_always_populated(self):
        """The audit trail is load-bearing — empty reason means the
        classifier is decorative."""
        c = classify_claim("any content")
        assert c.reason
        assert ";" in c.reason  # tier reason + magnitude reason joined

    def test_classification_is_frozen_dataclass(self):
        c = classify_claim("x")
        with pytest.raises(Exception):
            c.tier = Tier.PATTERN  # type: ignore[misc]


class TestMagnitudeHeuristics:
    def test_explicit_override_wins(self):
        c = classify_claim("trivial typo", explicit_magnitude=ClaimMagnitude.FOUNDATIONAL)
        assert c.magnitude == ClaimMagnitude.FOUNDATIONAL
        assert "explicit" in c.reason

    def test_trivial_keyword_detected(self):
        c = classify_claim("small fix to the cli output")
        assert c.magnitude == ClaimMagnitude.TRIVIAL

    def test_load_bearing_keyword_detected(self):
        c = classify_claim("foundational invariant of the architecture")
        assert c.magnitude == ClaimMagnitude.LOAD_BEARING

    def test_trivial_wins_over_load_bearing_when_both_present(self):
        """TRIVIAL is more specific — 'small fix to architecture' is
        still small."""
        c = classify_claim("small fix to the foundational architecture")
        assert c.magnitude == ClaimMagnitude.TRIVIAL

    def test_default_is_normal(self):
        c = classify_claim("a statement with no magnitude signal")
        assert c.magnitude == ClaimMagnitude.NORMAL


# ── warrant ──────────────────────────────────────────────────────────


class TestWarrantPersistence:
    def test_issue_round_trips(self):
        w = issue_warrant("claim-a", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4)
        loaded = get_warrant(w.warrant_id)
        assert loaded is not None
        assert loaded.warrant_id == w.warrant_id
        assert loaded.tier == Tier.FALSIFIABLE
        assert loaded.magnitude == ClaimMagnitude.NORMAL
        assert loaded.corroboration_count == 4

    def test_get_warrant_missing_returns_none(self):
        assert get_warrant("nonexistent") is None

    def test_get_warrants_for_claim_ordered(self):
        w1 = issue_warrant("claim-multi", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4)
        import time as _t

        _t.sleep(0.01)
        w2 = issue_warrant("claim-multi", Tier.FALSIFIABLE, ClaimMagnitude.LOAD_BEARING, 6)
        warrants = get_warrants_for_claim("claim-multi")
        assert [w.warrant_id for w in warrants] == [w1.warrant_id, w2.warrant_id]

    def test_warrant_id_prefix(self):
        w = issue_warrant("c", Tier.OUTCOME, ClaimMagnitude.NORMAL, 6)
        assert w.warrant_id.startswith("warrant-")


class TestWarrantChain:
    def test_genesis_warrant_has_no_previous(self):
        w = issue_warrant("c1", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4)
        assert w.previous_warrant_hash is None

    def test_second_warrant_chains_to_first(self):
        w1 = issue_warrant("c1", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4)
        w2 = issue_warrant("c2", Tier.PATTERN, ClaimMagnitude.NORMAL, 8)
        assert w2.previous_warrant_hash == w1.self_hash

    def test_verify_chain_passes_empty(self):
        verify_chain()  # no raise

    def test_verify_chain_passes_with_warrants(self):
        issue_warrant("c1", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4)
        issue_warrant("c2", Tier.OUTCOME, ClaimMagnitude.NORMAL, 6)
        issue_warrant("c3", Tier.PATTERN, ClaimMagnitude.NORMAL, 8)
        verify_chain()  # no raise

    def test_verify_chain_catches_content_tamper(self):
        w = issue_warrant("c1", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4)
        from divineos.core._ledger_base import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE gnosis_warrants SET corroboration_count = 999 WHERE warrant_id = ?",
                (w.warrant_id,),
            )
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(WarrantChainError, match="self_hash mismatch"):
            verify_chain()

    def test_verify_chain_catches_broken_link(self):
        """Tamper the previous_warrant_hash to create a chain break."""
        issue_warrant("c1", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4)
        w2 = issue_warrant("c2", Tier.OUTCOME, ClaimMagnitude.NORMAL, 6)

        from divineos.core._ledger_base import get_connection

        conn = get_connection()
        try:
            # Tamper previous_warrant_hash AND self_hash so the
            # content-check passes but the chain-check fails. If we
            # only change previous_warrant_hash, verify_self_hash
            # would catch it first.
            fake_prev = "0" * 64
            fake_self = GnosisWarrant._compute_self_hash(
                w2.claim_id,
                w2.tier,
                w2.magnitude,
                w2.corroboration_count,
                w2.council_count,
                w2.issued_at,
                fake_prev,
            )
            conn.execute(
                "UPDATE gnosis_warrants SET previous_warrant_hash = ?, self_hash = ? "
                "WHERE warrant_id = ?",
                (fake_prev, fake_self, w2.warrant_id),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(WarrantChainError, match="previous_warrant_hash"):
            verify_chain()


# ── routing ──────────────────────────────────────────────────────────


class _StubConvene:
    """Deterministic convene stub for routing tests."""

    def __init__(self, concerns: list[str]) -> None:
        self._c = concerns

    def shared_concerns(self) -> list[str]:
        return list(self._c)


class TestRouting:
    def test_trivial_requires_no_rounds(self):
        assert rounds_required(ClaimMagnitude.TRIVIAL) == 0

    def test_normal_requires_no_rounds(self):
        assert rounds_required(ClaimMagnitude.NORMAL) == 0

    def test_load_bearing_requires_one_round(self):
        assert rounds_required(ClaimMagnitude.LOAD_BEARING) == 1

    def test_foundational_requires_two_rounds(self):
        assert rounds_required(ClaimMagnitude.FOUNDATIONAL) == 2

    def test_trivial_approves_without_calling_council(self):
        called = {"n": 0}

        def spy(_content):
            called["n"] += 1
            return _StubConvene([])

        r = route_for_approval("x", ClaimMagnitude.TRIVIAL, convene_fn=spy)
        assert r.approved is True
        assert r.council_count == 0
        assert called["n"] == 0  # council never invoked

    def test_load_bearing_clean_approves(self):
        r = route_for_approval(
            "x", ClaimMagnitude.LOAD_BEARING, convene_fn=lambda _: _StubConvene([])
        )
        assert r.approved is True
        assert r.council_count == 1

    def test_load_bearing_with_concerns_blocks(self):
        r = route_for_approval(
            "x",
            ClaimMagnitude.LOAD_BEARING,
            convene_fn=lambda _: _StubConvene(["concern"]),
        )
        assert r.approved is False
        assert r.council_count == 0
        assert "BLOCKED" in r.rationale

    def test_foundational_requires_both_rounds(self):
        """One approved + one blocked = rejected."""
        idx = [0]

        def alternating(_content):
            idx[0] += 1
            return _StubConvene([] if idx[0] == 1 else ["second round concern"])

        r = route_for_approval("x", ClaimMagnitude.FOUNDATIONAL, convene_fn=alternating)
        assert r.approved is False
        assert r.council_count == 1
        # Both rounds named in rationale
        assert "round 1" in r.rationale
        assert "round 2" in r.rationale

    def test_foundational_both_clean_approves(self):
        r = route_for_approval(
            "x", ClaimMagnitude.FOUNDATIONAL, convene_fn=lambda _: _StubConvene([])
        )
        assert r.approved is True
        assert r.council_count == 2

    def test_rationale_never_empty(self):
        r = route_for_approval("x", ClaimMagnitude.TRIVIAL)
        assert r.rationale


# ── gate (full pipeline) ─────────────────────────────────────────────


class TestGate:
    def test_warrant_column_migration_idempotent(self):
        ensure_warrant_column_on_knowledge()
        ensure_warrant_column_on_knowledge()
        # Column exists
        from divineos.core._ledger_base import get_connection

        conn = get_connection()
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge)").fetchall()]
        finally:
            conn.close()
        assert "warrant_id" in cols

    def test_burden_rejection_returns_none_warrant(self):
        w, classification, routing = evaluate_and_warrant(
            claim_id="c1",
            content="threshold assert",  # falsifiable
            corroboration_count=1,  # below 4 required
            knowledge_type="FACT",
        )
        assert w is None
        assert classification.tier == Tier.FALSIFIABLE
        assert routing is None  # never reached

    def test_council_rejection_returns_none_warrant(self):
        w, c, r = evaluate_and_warrant(
            claim_id="c1",
            content="load-bearing architectural claim",
            corroboration_count=20,
            knowledge_type="PATTERN",
            convene_fn=lambda _: _StubConvene(["A", "B"]),
        )
        assert w is None
        assert r is not None
        assert r.approved is False

    def test_passes_with_warrant_issued(self):
        w, c, r = evaluate_and_warrant(
            claim_id="c1",
            content="measured threshold assert",
            corroboration_count=8,
            knowledge_type="FACT",
            source="measured",
            convene_fn=lambda _: _StubConvene([]),
        )
        assert w is not None
        assert w.tier == Tier.FALSIFIABLE
        # Persisted
        loaded = get_warrant(w.warrant_id)
        assert loaded is not None

    def test_adversarial_tier_raises_via_burden(self):
        """If anyone manages to force Tier.ADVERSARIAL (e.g. by calling
        the gate with explicit magnitude and classifier returning
        ADVERSARIAL somehow — currently it won't, but lock the
        behavior anyway), required_corroboration raises. Defensive."""
        with pytest.raises(NotImplementedError):
            required_corroboration(Tier.ADVERSARIAL, ClaimMagnitude.NORMAL)

    def test_record_warrant_on_knowledge_persists(self):
        from divineos.core.knowledge.crud import store_knowledge

        kid = store_knowledge(
            knowledge_type="FACT",
            content="measured threshold",
            confidence=0.8,
            source_events=["s1"],
            tags=[],
        )
        w = issue_warrant("direct-claim", Tier.FALSIFIABLE, ClaimMagnitude.NORMAL, 4)
        record_warrant_on_knowledge(kid, w.warrant_id)

        from divineos.core._ledger_base import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT warrant_id FROM knowledge WHERE knowledge_id = ?",
                (kid,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == w.warrant_id


# ── cross-module invariants ──────────────────────────────────────────


class TestInvariants:
    def test_valid_not_equal_true_disclaimer_present(self):
        """Pre-reg falsifier #5 protection — the docstring must
        explicitly name that warrants don't prove truth. If this
        test fails because someone edited the docstring, they need
        to rewrite both the test AND defend the change against the
        pre-reg."""
        import divineos.core.empirica
        import divineos.core.empirica.gate
        import divineos.core.empirica.types

        # At least one of the primary module docstrings must carry
        # the "valid != true" distinction explicitly.
        docstrings = (
            (divineos.core.empirica.__doc__ or "")
            + (divineos.core.empirica.types.__doc__ or "")
            + (divineos.core.empirica.gate.__doc__ or "")
            + (GnosisWarrant.__doc__ or "")
        )
        assert "true" in docstrings.lower()
        assert any(
            phrase in docstrings.lower()
            for phrase in (
                "does not prove",
                "not prove",
                "never treat",
                "proof-of-truth",
                "stand-in",
            )
        ), "valid-not-equal-true invariant disclaimer missing"

    def test_adversarial_tier_never_autoassigned_by_classifier(self):
        """Double-up: even if someone adds ADVERSARIAL triggers to
        the keyword list, classifier should still refuse to return
        it. Lock the invariant."""
        probe_contents = (
            "adversarial",
            "red team",
            "steelman",
            "nyarlathotep attacked this",
            "ADVERSARIAL TIER",
        )
        for content in probe_contents:
            c = classify_claim(content)
            assert c.tier != Tier.ADVERSARIAL, (
                f"classifier returned ADVERSARIAL for {content!r} — "
                "ADVERSARIAL must route through VOID, not heuristic"
            )

    def test_gate_cannot_issue_warrant_for_adversarial_tier(self):
        """If somehow a caller constructed an adversarial classification
        (e.g. through explicit future APIs), burden raises before warrant
        issue. The gate cannot rubber-stamp adversarial claims."""
        # Currently no public API for forcing ADVERSARIAL; this test
        # locks that the protection is at the burden layer, not the
        # classifier layer, so a future API that allowed forcing the
        # tier would still fail closed.
        with pytest.raises(NotImplementedError):
            required_corroboration(Tier.ADVERSARIAL, ClaimMagnitude.FOUNDATIONAL)
