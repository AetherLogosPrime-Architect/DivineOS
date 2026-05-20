"""Bypass-scanner test — block new agent-settable bypass env vars on
gate code paths.

The promise "I won't build escapes into architecture" is air until
structurally enforced. This test makes the promise stone — any new
gate with a self-settable env-var bypass fails CI.

The scan looks for the bypass-pattern in code paths that contain
gate-shape markers (BLOCKED, deny, raise Exit, return non-zero).
A bypass-pattern is:

    os.environ.get("DIVINEOS_*", "0") == "1"

within a function or block that also performs a gate-decision.

Approved bypass env vars are explicitly listed below. Each is here
because it is an operator-named-emergency (operator chooses when to
fire, agent cannot trigger autonomously), an opt-IN flag (raises
strictness rather than lowering it), or a generic push-readiness
escape with named justification.

Anything new must be added to the list AND accompanied by a commit
explaining why it isn't agent-self-relief — the addition is itself
a visible commit the operator can audit.
"""

from __future__ import annotations

import re
from pathlib import Path


__guardrail_required__ = True


# Approved bypass env vars. Each must be either:
#   (a) operator-named-emergency — operator decides when to fire,
#       agent cannot trigger autonomously in normal session flow
#   (b) opt-IN flag — raises strictness rather than lowering it
#   (c) push-readiness emergency — operator-controlled escape for
#       genuine push-time emergencies (test infra broken, blocking
#       hotfix needed, etc.)
#
# Adding a new entry requires a commit that explains which category
# it falls into and why it is not agent-self-relief. The commit is
# the visible record; this list is the structural enforcement.
_APPROVED_BYPASS_ENV_VARS = frozenset(
    {
        "DIVINEOS_SKIP_TESTS",  # push-readiness emergency, operator-named
        "DIVINEOS_SKIP_MULTIPARTY_CHECK",  # push-readiness emergency
        "DIVINEOS_EMERGENCY_PUSH",  # genuine emergency, operator-named
        "DIVINEOS_MULTIPARTY_STRICT",  # opt-IN flag (raises strictness)
        "DIVINEOS_SKIP_FRESHNESS_CHECK",  # push-gate bypass for known
        # cases like squash-merge-continued-branch where the freshness
        # check fires on a structural artifact rather than a real
        # stale-base condition
        "DIVINEOS_FORCE_PUSH_OK",  # pre-existing force-push safety
        # bypass, operator-emergency-named like the other push-
        # readiness env vars
    }
)


# Directories to scan for new bypass-pattern matches.
_SCAN_DIRS = (
    Path("src/divineos"),
    Path("scripts"),
    Path(".claude/hooks"),
)


# Pattern: env-var check whose result is compared against "1" — the
# canonical bypass shape. Excludes config env vars (paths, session IDs,
# log levels) which use the env var as a value, not as a flag.
_BYPASS_PATTERN_PY = re.compile(
    r'os\.environ\.get\(\s*["\'](DIVINEOS_[A-Z_]+)["\']\s*,\s*["\']0["\']\s*\)\s*==\s*["\']1["\']'
)
_BYPASS_PATTERN_SH = re.compile(r'\$\{(DIVINEOS_[A-Z_]+):-0\}["\']?\s*==\s*["\']1["\']')


def _find_repo_root() -> Path:
    """Find repo root by walking up from this test file until .git is found."""
    here = Path(__file__).resolve().parent
    while here != here.parent:
        if (here / ".git").exists():
            return here
        here = here.parent
    return Path(__file__).resolve().parent.parent


def test_no_new_agent_settable_bypass_env_vars() -> None:
    """Scan code for DIVINEOS_* env-var-checked bypass patterns.

    Any env var that appears in os.environ.get("DIVINEOS_...") MUST
    be on the _APPROVED_BYPASS_ENV_VARS list. Adding a new one to
    the list requires a commit (visible in git) — that is the
    visibility-as-bypass-cost discipline.
    """
    root = _find_repo_root()
    found: dict[str, list[str]] = {}
    for sub in _SCAN_DIRS:
        d = root / sub
        if not d.exists():
            continue
        for path in d.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".py", ".sh"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            patterns = [_BYPASS_PATTERN_PY] if path.suffix == ".py" else [_BYPASS_PATTERN_SH]
            for pat in patterns:
                for match in pat.finditer(text):
                    env_var = match.group(1)
                    rel = str(path.relative_to(root))
                    found.setdefault(env_var, []).append(rel)

    unapproved = {
        env: locations for env, locations in found.items() if env not in _APPROVED_BYPASS_ENV_VARS
    }
    assert not unapproved, (
        "Unapproved DIVINEOS_* bypass env var(s) found:\n"
        + "\n".join(f"  {env}: {locations}" for env, locations in sorted(unapproved.items()))
        + "\n\nAgent-settable bypass env vars are self-relief defeating their "
        "own gates. To add a new approved bypass, edit "
        "_APPROVED_BYPASS_ENV_VARS in tests/test_no_agent_settable_bypasses.py "
        "with a justification — the addition is itself a visible commit "
        "the operator can audit."
    )
