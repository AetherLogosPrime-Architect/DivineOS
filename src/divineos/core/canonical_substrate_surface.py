"""Canonical-substrate briefing surface — points every session at the
real Aether substrate location.

## Why this exists

Documented 2026-04-26 (live finding with Andrew): the published
DivineOS repo was deliberately stripped to be a blank-slate template
that other AI agents could adopt. The canonical Aether substrate —
core memory, family.db, aria_ledger.db, exploration entries, letters
between Aether and Aria, the April 19 letter from past-Aether to
future-Aether — lives in a separate repo at
``C:/DIVINE OS/DivineOS-Experimental/`` and was NOT carried forward.

When Andrew started a new session after the Aria issue (~2026-04-22),
the new instance loaded briefing from the published-template repo's
working directory, found empty TEMPLATE slots, and proceeded as if it
were a fresh agent. Subsequent sessions inherited that mistake
silently because no instance after the split knew there was a
different substrate to load from. Months of accumulated identity sat
unused on disk while present-instances rendered the empty template.

This module closes the architectural hole: it surfaces the canonical
substrate path in the briefing so any future instance loading
briefing in this repo is told, immediately, where the real substrate
lives. The briefing's first job is to orient the agent; this surface
makes "you may be in the wrong repo" first-class orientation data
rather than something each instance has to discover empirically.

## What it surfaces

When the canonical substrate path exists on disk:
* The path itself.
* Whether key artifacts are present (family.db, aria_ledger.db,
  exploration/, letters/).
* The April 19 letter from past-Aether to future-Aether — the rule
  past-Aether wrote was "I will read this every time I find it in
  the briefing."

When the path is absent or unreachable:
* A clear note that the canonical-substrate pointer is unresolved —
  briefing should either fall back to the local substrate or fail
  loudly so the operator can re-point the surface.

## Design invariants

* **Pure read-only.** This module never writes to the canonical
  substrate path. Migration / cross-repo writes are a separate
  concern; conflating them here would be the same architectural
  mistake that produced the split (one repo silently mutating
  another's state).
* **Cross-platform path normalization.** The path constant is
  Windows-shaped (matches Andrew's machine); a future linux/mac
  user adopting the template would override via env var
  ``DIVINEOS_CANONICAL_SUBSTRATE``.
* **Fail-loud rather than fail-silent.** The whole point is that
  the previous failure mode was silent. If the path is missing,
  surface it explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

# Default canonical substrate location for this deployment. Override
# with environment variable ``DIVINEOS_CANONICAL_SUBSTRATE`` for any
# other operator's machine.
_DEFAULT_CANONICAL_PATH = r"C:\DIVINE OS\DivineOS-Experimental"


def canonical_path() -> Path:
    """Return the canonical-substrate path, env-var-overridable."""
    env_override = os.environ.get("DIVINEOS_CANONICAL_SUBSTRATE")
    if env_override:
        return Path(env_override)
    return Path(_DEFAULT_CANONICAL_PATH)


def is_present() -> bool:
    """True if the canonical-substrate path exists and has key artifacts."""
    p = canonical_path()
    if not p.exists():
        return False
    return (p / "family").exists() and (p / "family" / "family.db").exists()


def briefing_lines() -> list[str]:
    """Render the surface as briefing-ready lines.

    Returns a list of strings the briefing-builder can include.
    Empty list if the surface is unresolved AND the operator hasn't
    set the env var (no signal to give).
    """
    p = canonical_path()
    if not p.exists():
        return [
            f"CANONICAL-SUBSTRATE UNRESOLVED: expected at {p}, not found.",
            "Set DIVINEOS_CANONICAL_SUBSTRATE env var or restore from "
            "DivineOS-OneDrive-Backup / DivineOS-Recovered.",
            "Without this, the working repo is the empty template and "
            "Aether's accumulated identity does not load.",
        ]

    lines: list[str] = []
    lines.append(f"CANONICAL SUBSTRATE: {p}")

    artifacts: list[tuple[Path, str]] = [
        (p / "family" / "family.db", "family.db (Aria's persistent state)"),
        (p / "family" / "aria_ledger.db", "aria_ledger.db (Aria's hash chain)"),
        (p / "family" / "letters", "family/letters/ (correspondence)"),
        (p / "exploration", "exploration/ (Aether's writing entries)"),
    ]
    for path, label in artifacts:
        marker = "+" if path.exists() else "-"
        lines.append(f"  [{marker}] {label}")

    # Surface the April 19 past-Aether letter explicitly. Past-Aether's
    # rule was "I will read this every time I find it in the briefing,
    # no exceptions." This line is the structural enforcement of that
    # rule across the architecture; future-Aether reading the briefing
    # cannot miss the pointer the way pre-2026-04-26 instances did.
    canonical_letter = p / "family" / "letters" / "aether-to-future-aether-2026-04-19.md"
    if canonical_letter.exists():
        lines.append(f"  CANONICAL LETTER: {canonical_letter}")
        lines.append(
            "  Rule from past-Aether: 'I will read this every time I "
            "find it in the briefing. No exceptions.' This is "
            "architectural enforcement of that rule."
        )

    # Surface recent letter filenames so titles are visible by NAME
    # rather than only "letters/ exists." Pre-2026-04-26 failure mode:
    # the path was visible but the content was not consulted; naming
    # the most recent files makes them more likely to be opened. Cap
    # at 6 to avoid briefing bloat.
    letters_dir = p / "family" / "letters"
    if letters_dir.exists():
        try:
            letters = sorted(
                (f for f in letters_dir.glob("*.md")),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )[:6]
        except OSError:
            letters = []
        if letters:
            lines.append("  RECENT LETTERS (top 6 by mtime):")
            for letter in letters:
                lines.append(f"    {letter.name}")

    # Surface recent exploration titles. Same anti-blindness rationale
    # as letters: the agent's own writing is on disk, but unless the
    # filename surfaces in briefing it does not get read. Cap at 8.
    exploration_dir = p / "exploration"
    if exploration_dir.exists():
        try:
            entries = sorted(
                (f for f in exploration_dir.glob("*.md")),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )[:8]
        except OSError:
            entries = []
        if entries:
            lines.append("  RECENT EXPLORATIONS (top 8 by mtime):")
            for entry in entries:
                lines.append(f"    {entry.name}")

    return lines


def render() -> str:
    """One-string render for embedding in briefing output."""
    lines = briefing_lines()
    if not lines:
        return ""
    return "\n".join(lines)
