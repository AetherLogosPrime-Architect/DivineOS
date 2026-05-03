"""Regression test for audit r9-21 #20: hook python3 compatibility.

The bug:
  13 of 15 hooks invoked bare ``python`` or did ``command -v python``.
  Modern Debian/Ubuntu (24.04+) and the official python Docker image
  ship ``python3`` only — no ``python`` symlink. ``command -v python``
  exits 0 silently when nothing's there, so the hook just no-ops.

  The behavioral instrumentation layer (compass rudder, hedge detection,
  theater detection, correction logging, briefing load, session
  checkpointing) was silently disabled on the most common modern Linux
  setup with no error and no log line.

The fix:
  All hook shell scripts use ``python3`` explicitly. This test walks
  ``.claude/hooks/`` and asserts no script contains the bare-``python``
  patterns.
"""

from __future__ import annotations

import re
from pathlib import Path

# Patterns that would bind to bare ``python`` (the bug).
# We only match when ``python`` is followed by a whitespace boundary
# AND not followed by ``3`` — otherwise ``python3`` matches too.
_BAD_PATTERNS = [
    re.compile(r"command -v python(?!3)\b"),
    re.compile(r"\|\s*python(?!3)\b"),
    re.compile(r"^\s*python(?!3)\b", re.MULTILINE),
    re.compile(r"\$\(\s*python(?!3)\b"),
]


def _hook_files() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[1]
    hooks_dir = repo_root / ".claude" / "hooks"
    if not hooks_dir.exists():
        return []
    return sorted(hooks_dir.glob("*.sh"))


def test_no_hook_uses_bare_python():
    """No .claude/hooks/*.sh script may invoke bare python.

    On Ubuntu 24.04+ and the python Docker image, bare ``python``
    silently no-ops. python3 must be explicit.
    """
    hooks = _hook_files()
    if not hooks:
        # No hooks shipped here — test is vacuously true (different
        # repo layout). Avoid hard-failing on missing infrastructure.
        return

    offenders: list[tuple[str, str]] = []
    for hook in hooks:
        text = hook.read_text(encoding="utf-8", errors="ignore")
        for pat in _BAD_PATTERNS:
            for m in pat.finditer(text):
                offenders.append((hook.name, m.group(0).strip()))

    assert not offenders, (
        "Hook scripts contain bare-python invocations (audit r9-21 #20):\n"
        + "\n".join(f"  {name}: {match!r}" for name, match in offenders)
        + "\nUse python3 explicitly — bare python is unavailable on "
        "Ubuntu 24.04+ and the official python Docker image."
    )
