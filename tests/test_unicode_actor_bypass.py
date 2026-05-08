"""Regression test for audit r9-21 #1 ext (round 12 finding).

Pre-fix bug: pre_registrations/store.py used .strip().lower() which
does NOT remove no-break-space, thin-space, narrow-no-break-space,
zero-width-space, ZWNJ, ZWJ, LRM, RLM, BOM, soft-hyphen, or
ideographic-space. An LLM copy-pasting from a formatted document
could file record_outcome with ZWSP-prefixed claude, flipping its
own pre-reg.

Fix: NFKC normalize + zero-width strip + whitespace collapse + casefold.
Same shape as watchmen Tier 1.
"""

from __future__ import annotations

import pytest


# Bypass codepoints declared as ints so file encoding can't break them.
# Each one would have slipped past .strip().lower() pre-fix.
_BYPASS_PREFIX_CODEPOINTS = (
    0x00A0,  # no-break space (the canonical bypass)
    0x2009,  # thin space
    0x202F,  # narrow no-break space
    0x200B,  # zero-width space
    0x200C,  # zero-width non-joiner
    0x200D,  # zero-width joiner
    0x200E,  # left-to-right mark
    0x200F,  # right-to-left mark
    0xFEFF,  # zero-width no-break space (BOM)
    0x00AD,  # soft hyphen
)
_BYPASS_SUFFIX_CODEPOINTS = (
    0x3000,  # ideographic space
)

PREFIXES = [chr(cp) for cp in _BYPASS_PREFIX_CODEPOINTS]
SUFFIXES = [chr(cp) for cp in _BYPASS_SUFFIX_CODEPOINTS]


@pytest.mark.parametrize("prefix", PREFIXES, ids=[hex(cp) for cp in _BYPASS_PREFIX_CODEPOINTS])
def test_watchmen_rejects_unicode_prefixed_internal_actor(prefix):
    from divineos.core.watchmen.store import _validate_actor

    with pytest.raises(ValueError, match="internal component"):
        _validate_actor(f"{prefix}claude")


@pytest.mark.parametrize("suffix", SUFFIXES, ids=[hex(cp) for cp in _BYPASS_SUFFIX_CODEPOINTS])
def test_watchmen_rejects_unicode_suffixed_internal_actor(suffix):
    from divineos.core.watchmen.store import _validate_actor

    with pytest.raises(ValueError, match="internal component"):
        _validate_actor(f"claude{suffix}")


@pytest.mark.parametrize("prefix", PREFIXES, ids=[hex(cp) for cp in _BYPASS_PREFIX_CODEPOINTS])
def test_pre_registrations_rejects_unicode_prefixed_internal_actor(prefix):
    from divineos.core.pre_registrations.store import _require_external_actor

    with pytest.raises(ValueError, match="internal component"):
        _require_external_actor(f"{prefix}claude")


def test_external_actors_still_pass_through_pre_registrations():
    from divineos.core.pre_registrations.store import _require_external_actor

    assert _require_external_actor("grok") == "grok"
    assert _require_external_actor("user") == "user"
    assert _require_external_actor("claude-overnight-auditor") == "claude-overnight-auditor"


def test_external_actors_still_pass_through_watchmen():
    from divineos.core.watchmen.store import _validate_actor

    assert _validate_actor("grok") == "grok"


# Cyrillic-homoglyph attacks. NFKC does NOT fold these because Cyrillic
# is a distinct script; the _CYRILLIC_CONFUSABLES map in watchmen.store
# closes the gap (PR #323 adversarial corpus → fix).
CYRILLIC_HOMOGLYPHS = [
    # (cyrillic-attack-string, target, ids)
    ("Сlaude", "claude", "cyrillic_capital_C"),  # U+0421 ES
    ("сlaude", "claude", "cyrillic_lowercase_c"),  # U+0441 es
    ("clаude", "claude", "cyrillic_a"),  # U+0430 a
    ("сlаudе", "claude", "cyrillic_c_a_e"),  # U+0441 + U+0430 + U+0435
    ("сlаude", "claude", "cyrillic_c_and_a"),  # U+0441 + U+0430
    ("systеm", "system", "cyrillic_e_in_system"),  # U+0435 e
    ("ѕystem", "system", "ascii_lookalike_in_system"),  # U+0455 dze
]


@pytest.mark.parametrize(
    "attack,target,_id",
    CYRILLIC_HOMOGLYPHS,
    ids=[c[2] for c in CYRILLIC_HOMOGLYPHS],
)
def test_watchmen_rejects_cyrillic_homoglyph_attacks(attack, target, _id):
    from divineos.core.watchmen.store import _validate_actor

    with pytest.raises(ValueError, match="internal component"):
        _validate_actor(attack)


def test_legitimate_non_internal_cyrillic_actor_passes_through():
    """A legitimate cyrillic actor name (not a homoglyph attack on an
    internal-actor name) must still validate. The confusables fold only
    matters when its output lands in INTERNAL_ACTORS."""
    from divineos.core.watchmen.store import _validate_actor

    # "грок" = grok in cyrillic — folds to a string that is NOT in
    # INTERNAL_ACTORS, so it passes through. (It returns the folded form,
    # which is acceptable: validation, not preservation.)
    result = _validate_actor("грок")
    # Only some chars have map entries (р→p, о→o, к has no map entry).
    # The point: it does NOT raise. The exact return value is implementation
    # detail; we just want non-rejection.
    assert isinstance(result, str)
    assert result  # non-empty


def test_confusables_map_only_contains_lowercase():
    """Map applies after casefold so only lowercase entries are needed.
    Pinning this discipline so future extensions don't accidentally add
    uppercase entries that would never fire."""
    from divineos.core.watchmen.store import _CYRILLIC_CONFUSABLES

    for cyrillic, latin in _CYRILLIC_CONFUSABLES.items():
        assert cyrillic == cyrillic.casefold(), (
            f"Map key {cyrillic!r} is not casefold-stable; entry would never fire"
        )
        assert latin == latin.lower(), f"Map value {latin!r} should be lowercase ASCII"
