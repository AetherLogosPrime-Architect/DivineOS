"""Audit r9-21 #39: canonical-set integrity tests.

Two failure modes the audit flagged:
  1. Allowlists referencing commands that don't exist (typo, renamed,
     deleted) — silently dead and only caught when the allowlist is
     consulted. Round-5 corrigibility allowlist had this shape.
  2. Two copies of the same canonical set drifting apart silently —
     pre_registrations.INTERNAL_ACTORS and watchmen.INTERNAL_ACTORS
     are intended to agree but nothing enforces it.

These tests pin both contracts so future drift fails loud.
"""

from __future__ import annotations


def test_internal_actor_lists_match_across_modules():
    """pre_registrations.INTERNAL_ACTORS must equal watchmen.INTERNAL_ACTORS.

    Both lists exist to reject self-audit (the running agent flipping its
    own pre-regs / filing its own findings). They MUST agree — a bypass
    closed in one but open in the other reopens the self-trigger hole.
    """
    from divineos.core.pre_registrations.types import (
        INTERNAL_ACTORS as PREREG_INTERNAL,
    )
    from divineos.core.watchmen.types import INTERNAL_ACTORS as WATCHMEN_INTERNAL

    assert PREREG_INTERNAL == WATCHMEN_INTERNAL, (
        f"INTERNAL_ACTORS divergence detected:\n"
        f"  pre_registrations only: {PREREG_INTERNAL - WATCHMEN_INTERNAL}\n"
        f"  watchmen only:          {WATCHMEN_INTERNAL - PREREG_INTERNAL}\n"
        "If divergence is intentional, the design requires reconciliation; "
        "the same actor name must mean the same thing in both modules."
    )


def test_corrigibility_always_allowed_references_real_commands():
    """Every entry in _ALWAYS_ALLOWED must be a registered CLI command.

    A typo or rename that orphans an entry creates a silent off-switch
    trap: EMERGENCY_STOP could refuse a command the design expected to
    bypass. Audit r9-21 round 5 caught exactly this shape — `extract`
    was documented as allowed but missing from the set.
    """
    from divineos.cli import cli as cli_root
    from divineos.core.corrigibility import _ALWAYS_ALLOWED

    real_commands = set(cli_root.commands.keys())
    real_commands.update({"--help", "-h"})  # Click meta-flags, not commands

    missing = {c for c in _ALWAYS_ALLOWED if c not in real_commands}
    assert not missing, (
        f"_ALWAYS_ALLOWED references non-existent commands: {sorted(missing)}. "
        "Either add the missing CLI command, fix the typo, or remove the "
        "orphan entry."
    )


def test_corrigibility_read_only_references_real_commands():
    """Every entry in _READ_ONLY_COMMANDS must be a registered CLI command."""
    from divineos.cli import cli as cli_root
    from divineos.core.corrigibility import _READ_ONLY_COMMANDS

    real_commands = set(cli_root.commands.keys())
    missing = {c for c in _READ_ONLY_COMMANDS if c not in real_commands}
    assert not missing, (
        f"_READ_ONLY_COMMANDS references non-existent commands: {sorted(missing)}. "
        "Audit r9-21 #39 — canonical set must reference real CLI."
    )
