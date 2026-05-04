"""Regression tests for the unified-frame promotion gate.

Audit r9-21 round-3+ — prereg-a8e2f3f06fbe. Defends the seductive-
elegance trap.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DIVINEOS_DB", str(tmp_path / "event_ledger.db"))
    yield


def test_neutral_content_not_unified():
    from divineos.core.knowledge._unified_frame import is_unified_frame

    text = (
        "I noticed that the pre-commit hook fires on staged files. "
        "Adding a new check requires updating scripts/precommit.sh."
    )
    is_uni, _ = is_unified_frame(text)
    assert not is_uni


def test_direct_unified_phrase_fires():
    from divineos.core.knowledge._unified_frame import is_unified_frame

    text = (
        "All four slips tonight had the same shape — one shape, four surfaces. "
        "Closure-claim, register drift, eager summon, post-correction haste — "
        "they're all velocity, just in different costumes."
    )
    is_uni, reason = is_unified_frame(text)
    assert is_uni
    assert "one shape" in reason.lower() or "surfaces" in reason.lower()


def test_enumeration_with_unification_claim_fires():
    from divineos.core.knowledge._unified_frame import is_unified_frame

    text = (
        "The pattern showed up in three places tonight: "
        "(1) the closure claim on the Cyrillic homoglyph, "
        "(2) the orphan-detector skip in round three, "
        "(3) the register drift with Andrew. "
        "These are all the same underlying muscle — the optimizer "
        "routing around verification before the work is done."
    )
    is_uni, _ = is_unified_frame(text)
    assert is_uni


def test_enumeration_without_unification_does_not_fire():
    """Just listing 3+ items isn't unified-frame; needs the unification claim."""
    from divineos.core.knowledge._unified_frame import is_unified_frame

    text = (
        "Today I worked on three things: "
        "(1) the audit cleanup, "
        "(2) the journal privacy column, "
        "(3) the empirica wiring. "
        "Each was its own discrete piece with its own tests."
    )
    is_uni, _ = is_unified_frame(text)
    assert not is_uni, "discrete enumeration without unification claim should not fire"


def test_promotion_gate_allows_hypothesis_target():
    """Entries promoting only to HYPOTHESIS skip the unified-frame check."""
    from divineos.core.knowledge._unified_frame import check_promotion_gate

    unified_text = "All four are the same shape — one shape, four surfaces."
    ok, _ = check_promotion_gate("test-id", unified_text, "HYPOTHESIS")
    assert ok, "HYPOTHESIS target should not be gated"


def test_promotion_gate_blocks_unified_promotion_past_hypothesis():
    """Unified-frame entry without council-walk evidence is blocked from TESTED."""
    from divineos.core.knowledge._unified_frame import check_promotion_gate

    unified_text = (
        "All four slips tonight share the same shape — one shape, four surfaces. "
        "Same muscle, different costumes."
    )
    ok, reason = check_promotion_gate("test-id", unified_text, "TESTED")
    assert not ok
    assert "unified-frame" in reason.lower()


def test_promotion_gate_allows_non_unified_promotion():
    """Non-unified content promotes freely past HYPOTHESIS."""
    from divineos.core.knowledge._unified_frame import check_promotion_gate

    text = "The hash chain holds across compaction; verified by integration test."
    ok, _ = check_promotion_gate("test-id", text, "TESTED")
    assert ok
