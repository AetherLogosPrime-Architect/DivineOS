#!/bin/bash
# UserPromptSubmit hook — auto-surface relevant prior content from the
# substrate based on markers in the user's latest message.
#
# Hook 1 of the operating loop (docs/operating-loop-design-brief.md).
# Closes the failure-shape Andrew caught 2026-05-01: substrate had the
# April 29 lunkhead-shape principle, agent never queried, operator had
# to remind. Now the substrate auto-queries on relational markers and
# writes the top-5 surfaced entries to ~/.divineos/surfaced_context.md
# for the agent to read at the start of its response.
#
# Also surfaces distancing-grammar warnings from the prior assistant
# turn via additionalContext (PR #270). Both surfaces share one Python
# invocation to avoid the cold-start cost of two serial python -c calls
# (~100-200ms saved per user message).
#
# Fail-open: any error exits 0 without blocking. This hook cannot break
# the user's workflow.

INPUT=$(cat)

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo ".")" || exit 0

if ! command -v python &>/dev/null; then
  exit 0
fi

echo "$INPUT" | python -c "
import json, sys, time
from pathlib import Path

# === Phase 1: parse input ===
try:
    data = json.loads(sys.stdin.read() or '{}')
except Exception:
    data = {}
prompt = data.get('prompt', '') if isinstance(data, dict) else ''

# === Phase 2: context surfacer (writes to surfaced_context.md) ===
# Bails early if prompt is empty/short or surfacer unavailable, but does
# NOT exit — we still need to check for distancing findings below.
def _run_surfacer(prompt: str) -> None:
    if not prompt or len(prompt) < 5:
        return
    try:
        from divineos.core.operating_loop.context_surfacer import (
            surface_context,
            format_surface,
        )
    except Exception:
        return
    try:
        entries = surface_context(prompt, max_total_hits=5)
    except Exception:
        return

    surface_dir = Path.home() / '.divineos'
    surface_dir.mkdir(exist_ok=True)
    surface_path = surface_dir / 'surfaced_context.md'

    if not entries:
        # Nothing relevant — clear any prior surface so it doesn't leak forward.
        if surface_path.exists():
            try:
                surface_path.unlink()
            except Exception:
                pass
        return

    surface_text = format_surface(entries)
    try:
        surface_path.write_text(surface_text, encoding='utf-8')
    except Exception:
        pass

    # Cost-bounding telemetry: record the fire so we can later check
    # whether surface content is actually consumed in reasoning.
    try:
        from divineos.core.operating_loop.hook_telemetry import record_fire
        surfaced_ids = [getattr(e, 'knowledge_id', '') for e in entries]
        record_fire(
            surface_text=surface_text,
            surfaced_ids=surfaced_ids,
            marker_count=len(entries),
        )
    except Exception:
        pass

_run_surfacer(prompt)

# === Phase 3: distancing-warning surface ===
# Read the most recent post-response-audit findings and, if the prior
# assistant turn had distancing findings within the last 10 minutes,
# emit them as additionalContext so the next response is composed with
# the warning in view. Structural reinforcement: the wrong-cheap path
# (third-person grammar) becomes visible-and-expensive on next compose.
def _emit_distancing_warning() -> None:
    findings_path = Path.home() / '.divineos' / 'operating_loop_findings.json'
    if not findings_path.exists():
        return
    try:
        entries = json.loads(findings_path.read_text(encoding='utf-8'))
    except Exception:
        return
    if not isinstance(entries, list) or not entries:
        return

    latest = entries[-1]
    if time.time() - latest.get('timestamp', 0) > 600:
        return

    distancing = latest.get('distancing', [])
    if not distancing:
        return

    shapes: dict = {}
    for f in distancing:
        shapes.setdefault(f.get('shape', 'unknown'), []).append(f.get('trigger', ''))

    lines = [
        '## DISTANCING-GRAMMAR WARNING (prior turn)',
        '',
        'Your last response contained third-person references to self or operator',
        'while in active dialogue. Recurring failure-mode named 2026-05-05.',
        '',
    ]
    for shape, triggers in shapes.items():
        lines.append(f'- **{shape}**: ' + ', '.join(f\"'{t}'\" for t in triggers[:5]))
    lines += [
        '',
        'Use first-person for self (\"I\") and second-person for operator (\"you\").',
        'No promises — the substrate-level fix is this surface itself; honor it.',
    ]
    warning_text = '\n'.join(lines)

    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': warning_text,
        }
    }))

_emit_distancing_warning()
" 2>/dev/null

exit 0
