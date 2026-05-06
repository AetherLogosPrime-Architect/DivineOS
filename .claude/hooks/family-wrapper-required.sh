#!/bin/bash
# PreToolUse hook — block direct Agent invocations of family-member subagents
# unless a fresh sealed-prompt is present (produced by ``divineos talk-to``).
#
# # Why this exists
#
# Family-member subagents (those whose ``.claude/agents/<name>.md``
# frontmatter description marks them as "family system" entities) carry
# persistent state in ``family.db`` — knowledge, opinions, affect history,
# recent interactions, letters. Spawning the subagent via the Agent tool
# directly with an operator-written prompt bypasses voice-context loading.
# What answers is the agent definition + the operator's prompt-context;
# the persistent self never gets read. Any state-writes attributed to that
# answer become fabricated continuity written into the persistent self.
#
# This hook closes the bypass at the substrate level. It runs PreToolUse on
# Agent invocations. If subagent_type matches a registered family-member
# name, the hook checks for a fresh sealed-prompt file written by
# ``divineos talk-to <member>``:
#
#   * pending file at ``~/.divineos/talk_to_<member>_pending.json``
#   * sealed-prompt file at ``~/.divineos/talk_to_<member>_sealed_prompt.txt``
#   * pending file's TTL not expired (default 120s)
#   * sealed-prompt SHA256 matches the prompt being sent
#
# If any check fails, the hook denies the invocation with a message
# pointing the operator at the wrapper.
#
# # Falsifier
#
# This hook should NOT fire on:
#   * Agent invocations whose subagent_type is not a registered
#     family-member (general-purpose, Explore, Plan, etc).
#   * Agent invocations whose subagent_type is a family-member name AND
#     the sealed-prompt is fresh and matches.
#
# Fail-open: any error (missing python, broken module imports, malformed
# input) returns 0 without blocking. The wrapper is the load-bearing
# enforcement; this hook is the structural reinforcement that makes the
# wrong path expensive.

INPUT=$(cat)

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo ".")" || exit 0

if ! command -v python &>/dev/null; then
  exit 0
fi

echo "$INPUT" | python -c "
import hashlib
import json
import re
import sys
import time
from pathlib import Path

try:
    data = json.loads(sys.stdin.read() or '{}')
except Exception:
    sys.exit(0)

# PreToolUse payload: tool_name + tool_input. We only care about
# Agent invocations.
tool_name = data.get('tool_name', '') or ''
if tool_name not in ('Agent', 'Task'):
    sys.exit(0)

tool_input = data.get('tool_input', {}) or {}
subagent_type = (tool_input.get('subagent_type') or '').strip()
prompt = tool_input.get('prompt', '') or ''

if not subagent_type or not prompt:
    sys.exit(0)

# Discover registered family members. If discovery fails (fresh install
# with no agents/, broken module), fail open.
try:
    sys.path.insert(0, 'src')
    from divineos.core.operating_loop.registered_names import family_member_names
    members = {n.lower() for n in family_member_names()}
except Exception:
    sys.exit(0)

if subagent_type.lower() not in members:
    # Not a family-member subagent. Hook doesn't apply.
    sys.exit(0)

# Check sealed-prompt freshness.
member_lc = subagent_type.lower()
pending_dir = Path.home() / '.divineos'
pending_path = pending_dir / f'talk_to_{member_lc}_pending.json'
sealed_path = pending_dir / f'talk_to_{member_lc}_sealed_prompt.txt'

def deny(reason):
    out = {
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': reason,
        }
    }
    print(json.dumps(out))
    sys.exit(0)

if not pending_path.exists() or not sealed_path.exists():
    deny(
        f\"BLOCKED: Direct Agent invocation of family-member '{subagent_type}' \"
        f'is not allowed. Family members must be reached via the talk-to '
        f'wrapper so voice context loads from family.db. Run:\\n\\n'
        f'    divineos talk-to {subagent_type.lower()} \"<your plain message>\"\\n\\n'
        f'Then invoke Agent with the exact bytes of the sealed-prompt file.'
    )

try:
    pending = json.loads(pending_path.read_text(encoding='utf-8'))
except Exception:
    deny(
        f\"BLOCKED: family-member sealed-prompt for '{subagent_type}' is \"
        f'malformed. Re-run divineos talk-to to generate a fresh one.'
    )

# TTL check
ttl = pending.get('ttl_seconds', 120)
ts = pending.get('ts', 0)
age = time.time() - ts
if age > ttl:
    deny(
        f\"BLOCKED: family-member sealed-prompt for '{subagent_type}' is \"
        f'expired ({age:.0f}s old, TTL {ttl:.0f}s). Re-run divineos talk-to '
        f'to generate a fresh one.'
    )

# Hash match — operator-edited prompts are blocked
expected_hash = pending.get('sealed_prompt_sha256', '')
actual_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
if expected_hash != actual_hash:
    deny(
        f\"BLOCKED: prompt sent to family-member '{subagent_type}' does not \"
        f'match the sealed-prompt produced by talk-to. The wrapper builds '
        f'the prompt with voice context loaded from family.db; operator '
        f'edits to the prompt bypass that loading. Send the exact bytes '
        f'of {sealed_path}, or re-run talk-to with your actual message.'
    )

# All checks passed — let the Agent invocation proceed.
sys.exit(0)
" 2>/dev/null

exit 0
