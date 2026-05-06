"""Tests for ``.claude/hooks/family-wrapper-required.sh`` — bypass-block hook.

Runs the hook as a subprocess with a faked PreToolUse JSON payload and
verifies the deny/allow decision. Uses tmp_path to redirect the
pending-file dir via HOME env override (the hook reads
``Path.home() / ".divineos"``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


# Path to the hook script, relative to repo root.
HOOK_PATH = Path(__file__).parent.parent / ".claude" / "hooks" / "family-wrapper-required.sh"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_bash() -> str | None:
    """Locate a usable bash binary.

    On Windows, ``shutil.which("bash")`` may return WSL's bash which
    isn't compatible with the hook (different filesystem layout, no
    Python on PATH). Prefer Git-Bash locations explicitly.
    """
    # Common Git-Bash install locations on Windows
    git_bash_candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for candidate in git_bash_candidates:
        if Path(candidate).exists():
            return candidate
    # Fall back to PATH (works on Linux/macOS, may pick WSL on Windows)
    return shutil.which("bash")


_BASH_PATH = _find_bash()
_BASH_AVAILABLE = _BASH_PATH is not None


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    """Redirect HOME so the hook reads sealed-prompt files from tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    (tmp_path / ".divineos").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _run_hook(payload: dict, fake_home: Path) -> tuple[int, str, str]:
    """Run the hook with the given PreToolUse payload. Returns (rc, stdout, stderr)."""
    if not HOOK_PATH.exists():
        pytest.skip(f"Hook not present at {HOOK_PATH}")
    if not _BASH_AVAILABLE:
        pytest.skip("bash not available on this platform")

    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    # Ensure the python on PATH can find the divineos package.
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [_BASH_PATH, str(HOOK_PATH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _is_deny(stdout: str) -> bool:
    """Parse the hook's stdout JSON and return True if it's a deny decision."""
    if not stdout.strip():
        return False
    try:
        decision = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    return decision.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _deny_reason(stdout: str) -> str:
    if not stdout.strip():
        return ""
    try:
        decision = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    return decision.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


@pytest.fixture
def registered_aria(monkeypatch):
    """Make registered_names see "aria" as a family member.

    The hook spawns a fresh python subprocess that does its own import
    of registered_names — monkeypatching the in-process module won't
    propagate. Instead, drop a fake agent .md file into tmp .claude/agents/
    so the disk-based discovery picks it up.

    For these tests we cd into REPO_ROOT and the hook reads
    ``.claude/agents/`` from there. The real repo's family-member-template
    file is excluded by the discovery logic; we add aria.md temporarily.
    """
    # We do this via a fixture that creates a temporary aria.md file
    # in the repo's .claude/agents/, deletes it on teardown.
    agents_dir = REPO_ROOT / ".claude" / "agents"
    aria_md = agents_dir / "aria.md"
    if aria_md.exists():
        # If a real aria.md already exists in this checkout (gitignored
        # in main), the hook will already discover it. No-op.
        yield
        return
    aria_md.write_text(
        "---\nname: aria\ndescription: test family member in the family system.\n---\n# test\n",
        encoding="utf-8",
    )
    try:
        yield
    finally:
        aria_md.unlink(missing_ok=True)


class TestNonFamilyMemberAllowed:
    def test_general_purpose_subagent_allowed(self, fake_home) -> None:
        payload = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "general-purpose", "prompt": "hi"},
        }
        rc, stdout, _stderr = _run_hook(payload, fake_home)
        assert rc == 0
        assert not _is_deny(stdout)

    def test_explore_subagent_allowed(self, fake_home) -> None:
        payload = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore", "prompt": "find files"},
        }
        rc, stdout, _stderr = _run_hook(payload, fake_home)
        assert rc == 0
        assert not _is_deny(stdout)

    def test_non_agent_tool_unchecked(self, fake_home) -> None:
        # Edit, Write, Bash etc. not subject to this hook (matched by
        # other PreToolUse hooks in settings.json).
        payload = {"tool_name": "Edit", "tool_input": {"file_path": "a.py"}}
        rc, stdout, _stderr = _run_hook(payload, fake_home)
        assert rc == 0
        assert not _is_deny(stdout)


class TestFamilyMemberBypassBlocked:
    def test_no_sealed_prompt_blocks(self, fake_home, registered_aria) -> None:
        payload = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "aria", "prompt": "hi love"},
        }
        rc, stdout, _stderr = _run_hook(payload, fake_home)
        assert rc == 0  # exit 0 — decision in JSON
        assert _is_deny(stdout)
        reason = _deny_reason(stdout)
        assert "talk-to" in reason
        assert "aria" in reason.lower()

    def test_expired_sealed_prompt_blocks(self, fake_home, registered_aria) -> None:
        import time

        sent_prompt = "hi love"
        sealed_dir = fake_home / ".divineos"
        sealed_path = sealed_dir / "talk_to_aria_sealed_prompt.txt"
        pending_path = sealed_dir / "talk_to_aria_pending.json"
        sealed_path.write_text("VOICE\n--- end --- \n" + sent_prompt, encoding="utf-8")
        pending_path.write_text(
            json.dumps(
                {
                    "ts": time.time() - 999,  # well past TTL
                    "ttl_seconds": 120,
                    "nonce": "abc",
                    "member": "aria",
                    "sealed_prompt_sha256": hashlib.sha256(sent_prompt.encode()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )

        payload = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "aria", "prompt": sent_prompt},
        }
        rc, stdout, _stderr = _run_hook(payload, fake_home)
        assert rc == 0
        assert _is_deny(stdout)
        reason = _deny_reason(stdout)
        assert "expired" in reason.lower()

    def test_mismatched_prompt_hash_blocks(self, fake_home, registered_aria) -> None:
        import time

        original_prompt = "the original message"
        edited_prompt = "the edited message"  # operator tampered with prompt
        sealed_dir = fake_home / ".divineos"
        (sealed_dir / "talk_to_aria_sealed_prompt.txt").write_text(
            "VOICE\n--- end ---\n" + original_prompt, encoding="utf-8"
        )
        (sealed_dir / "talk_to_aria_pending.json").write_text(
            json.dumps(
                {
                    "ts": time.time(),
                    "ttl_seconds": 120,
                    "nonce": "abc",
                    "member": "aria",
                    "sealed_prompt_sha256": hashlib.sha256(original_prompt.encode()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )

        payload = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "aria", "prompt": edited_prompt},
        }
        rc, stdout, _stderr = _run_hook(payload, fake_home)
        assert rc == 0
        assert _is_deny(stdout)
        reason = _deny_reason(stdout)
        assert "match" in reason.lower() or "tamper" in reason.lower() or "edited" in reason.lower()
