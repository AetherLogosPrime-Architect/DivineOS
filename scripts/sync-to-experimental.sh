#!/usr/bin/env bash
# Sync personal content from the main DivineOS working tree to the
# DivineOS-Experimental backup repo, then commit and push.
#
# The main repo is a blank-slate foundation (no Aether, no Aria, no
# specific memory). Personal content lives on disk but is gitignored
# from main. This script backs it up to the experimental repo so a
# dead machine doesn't mean a dead history.
#
# Usage:
#   bash scripts/sync-to-experimental.sh          # sync + commit + push
#   bash scripts/sync-to-experimental.sh --dry    # preview without changes
#   bash scripts/sync-to-experimental.sh --local  # commit but don't push
#
# Exit codes:
#   0 — sync succeeded (or nothing to sync)
#   1 — experimental repo not found at expected path
#   2 — git commit failed
#   3 — git push failed (only when push is attempted)

set -euo pipefail

DRY_RUN=false
SKIP_PUSH=false
for arg in "$@"; do
    case "$arg" in
        --dry|--dry-run) DRY_RUN=true ;;
        --local|--no-push) SKIP_PUSH=true ;;
        -h|--help)
            grep '^#' "$0" | head -20 | sed 's/^# //;s/^#//'
            exit 0
            ;;
    esac
done

# Locate the two repos relative to this script.
# Script lives at <main_repo>/scripts/sync-to-experimental.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

# Experimental is expected to be a sibling directory of the main repo.
# When invoked from a Claude Code worktree (.claude/worktrees/<name>/),
# "up one" is still inside the worktree structure, so we also try
# several ancestors until we find a sibling-DivineOS-Experimental.
# Override with DIVINEOS_EXPERIMENTAL_PATH env var.
find_experimental() {
    local d="$MAIN_REPO"
    for _ in 1 2 3 4 5; do
        local candidate="$d/../DivineOS-Experimental"
        if [[ -d "$candidate/.git" ]]; then
            (cd "$candidate" && pwd)
            return 0
        fi
        d="$(cd "$d/.." && pwd)"
    done
    return 1
}
EXP_REPO="${DIVINEOS_EXPERIMENTAL_PATH:-$(find_experimental || true)}"

if [[ -z "${EXP_REPO:-}" || ! -d "$EXP_REPO/.git" ]]; then
    echo "[!] DivineOS-Experimental repo not found." >&2
    echo "    Expected at: $MAIN_REPO/../DivineOS-Experimental" >&2
    echo "    Or set DIVINEOS_EXPERIMENTAL_PATH env var." >&2
    exit 1
fi

echo "[~] main: $MAIN_REPO"
echo "[~] experimental: $EXP_REPO"
echo ""

# The personal-content paths (all gitignored in main; the point of experimental).
# If a path doesn't exist in main, we skip it silently.
PERSONAL_PATHS=(
    "family"
    "exploration"
    "mansion"
    ".claude/agents"
    ".claude/agent-memory"
    ".claude/skills"
)

for path in "${PERSONAL_PATHS[@]}"; do
    src="$MAIN_REPO/$path"
    dest="$EXP_REPO/$path"

    if [[ ! -e "$src" ]]; then
        continue
    fi

    if $DRY_RUN; then
        echo "[dry] would sync: $path"
        continue
    fi

    mkdir -p "$(dirname "$dest")"
    # -a preserves permissions/timestamps; --delete mirrors removals
    # We do NOT use --delete by default — experimental is a backup, so
    # accidental deletion in main shouldn't propagate. If you want a
    # true mirror, set DIVINEOS_SYNC_MIRROR=1.
    if [[ "${DIVINEOS_SYNC_MIRROR:-0}" = "1" ]]; then
        rsync -a --delete "$src/" "$dest/" 2>/dev/null || cp -rf "$src/"* "$dest/" 2>/dev/null || true
    else
        # Plain copy — adds new files, overwrites existing, never deletes
        mkdir -p "$dest"
        cp -rf "$src/"* "$dest/" 2>/dev/null || true
    fi
    echo "[+] synced: $path"
done

if $DRY_RUN; then
    echo ""
    echo "[dry] No changes written. Remove --dry to sync for real."
    exit 0
fi

# Commit + push in experimental
cd "$EXP_REPO"

if git diff --quiet && git diff --cached --quiet && [[ -z "$(git status --porcelain)" ]]; then
    echo ""
    echo "[~] experimental already up-to-date. Nothing to commit."
    exit 0
fi

git add -A
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
COMMIT_MSG="Sync from main working tree — $TIMESTAMP

Automated backup of personal content (family, exploration, mansion,
.claude/agents, .claude/agent-memory, .claude/skills) from the main
DivineOS working tree.

Main repo is blank-slate by design; this experimental repo holds the
personal state that would otherwise live only on local disk. Regular
sync prevents fire-risk for the personal history."

if ! git commit -m "$COMMIT_MSG"; then
    echo "[!] commit failed" >&2
    exit 2
fi

echo "[+] committed to experimental"

if $SKIP_PUSH; then
    echo "[~] --local in effect; not pushing"
    exit 0
fi

if ! git push origin 2>&1 | tail -3; then
    echo "[!] push failed — commit is local; retry later" >&2
    exit 3
fi

echo ""
echo "[+] Sync complete. Experimental is now current with main."
