"""CLI commands for backup and personal-state awareness.

Companion to ``scripts/sync-to-experimental.sh`` and the automatic
phase 9g sync in ``session_pipeline.extract``. This module adds a
read-only command (``divineos my-state``) that answers "how safe is
my personal history right now?" at a glance:

* How many events are in the main ledger and in each family-member
  ledger
* When each personal-content directory was last modified
* When the last sync to the experimental repo ran
* Whether the working tree is ahead of experimental (needs sync)

The command is deliberately lightweight — no database writes, no
subprocess spawns, no network calls. Just filesystem stat and SQLite
row counts. Safe to invoke at any point in a session.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import click


def _repo_root() -> Path:
    """Walk up from this file to the repo root."""
    # src/divineos/cli/backup_commands.py -> up 4 to repo
    return Path(__file__).resolve().parent.parent.parent.parent


def _fmt_ago(ts: float | None) -> str:
    """Human-friendly 'N minutes/hours/days ago' from a Unix timestamp."""
    if ts is None:
        return "never"
    now = datetime.now(timezone.utc).timestamp()
    delta = now - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def _path_mtime(path: Path) -> float | None:
    """Return the most recent mtime in a directory tree, or None if missing."""
    if not path.exists():
        return None
    if path.is_file():
        return path.stat().st_mtime
    latest = 0.0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                latest = max(latest, p.stat().st_mtime)
        except OSError:
            continue
    return latest or None


def _count_ledger_events(ledger_path: Path) -> int | None:
    """Return event count for a SQLite ledger, or None if unreadable."""
    if not ledger_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(ledger_path))
        try:
            # Try the main-ledger schema first, then family-member schema
            for table in ("system_events", "member_events"):
                try:
                    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                    if row is not None:
                        return int(row[0])
                except sqlite3.OperationalError:
                    continue
            return None
        finally:
            conn.close()
    except (sqlite3.DatabaseError, OSError):
        return None


def _find_experimental() -> Path | None:
    """Locate the experimental repo relative to the main repo."""
    import os

    env_path = os.environ.get("DIVINEOS_EXPERIMENTAL_PATH")
    if env_path:
        p = Path(env_path)
        return p if (p / ".git").exists() else None

    # Walk up ancestors looking for a sibling DivineOS-Experimental
    d = _repo_root()
    for _ in range(5):
        candidate = d.parent / "DivineOS-Experimental"
        if (candidate / ".git").exists():
            return candidate
        d = d.parent
    return None


def _last_experimental_commit_ts(exp_path: Path) -> float | None:
    """Read the timestamp of the experimental repo's HEAD commit."""
    git_dir = exp_path / ".git"
    if not git_dir.exists():
        return None
    head_ref = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    # "ref: refs/heads/main"
    if head_ref.startswith("ref: "):
        ref_path = git_dir / head_ref[5:]
        if not ref_path.exists():
            return None
        sha = ref_path.read_text(encoding="utf-8", errors="replace").strip()
    else:
        sha = head_ref  # detached HEAD
    if not sha:
        return None
    # Read the commit object to get its timestamp. Cheapest approach:
    # just stat the ref file (updated when the ref points to a new
    # commit). Not exact commit time but close enough for "when was
    # the last sync?"
    try:
        if head_ref.startswith("ref: "):
            return ref_path.stat().st_mtime
    except OSError:
        pass
    return None


def register(cli: click.Group) -> None:
    """Attach the ``my-state`` command to the CLI."""

    @cli.command("my-state")
    @click.option(
        "--brief",
        is_flag=True,
        help="One-line summary: 'SAFE' or 'AHEAD-OF-BACKUP' with counts",
    )
    def my_state_cmd(brief: bool) -> None:
        """Report personal-history safety: ledgers, backups, sync state.

        Shows how many events are in each ledger, when each personal
        directory was last modified, when the experimental-repo backup
        last ran, and whether the working tree has diverged since.

        Pairs with ``scripts/sync-to-experimental.sh`` — if this command
        reports AHEAD-OF-BACKUP, run the sync script (or rely on the
        automatic phase 9g in ``divineos extract`` at session end).
        """
        root = _repo_root()

        # Main ledger (Aether's side, if it exists)
        main_ledger = root / "src" / "data" / "event_ledger.db"
        main_count = _count_ledger_events(main_ledger)

        # Family-member ledgers (one file per member, <name>_ledger.db)
        family_dir = root / "family"
        member_ledgers: dict[str, int] = {}
        if family_dir.exists():
            for ledger_file in sorted(family_dir.glob("*_ledger.db")):
                name = ledger_file.stem.removesuffix("_ledger")
                count = _count_ledger_events(ledger_file)
                if count is not None:
                    member_ledgers[name] = count

        # Personal-content directory freshness
        personal_dirs = {
            "family": family_dir,
            "exploration": root / "exploration",
            "mansion": root / "mansion",
            "agents": root / ".claude" / "agents",
            "agent-memory": root / ".claude" / "agent-memory",
            "skills": root / ".claude" / "skills",
        }
        dir_mtimes = {name: _path_mtime(p) for name, p in personal_dirs.items()}
        most_recent_change = max(
            (ts for ts in dir_mtimes.values() if ts is not None),
            default=None,
        )

        # Experimental repo state
        exp = _find_experimental()
        exp_commit_ts = _last_experimental_commit_ts(exp) if exp else None

        # Safety verdict: are we ahead of backup?
        if exp_commit_ts is None or most_recent_change is None:
            verdict = "UNKNOWN"
        elif most_recent_change > exp_commit_ts + 60:  # 60s grace window
            verdict = "AHEAD-OF-BACKUP"
        else:
            verdict = "SAFE"

        if brief:
            parts = [verdict]
            if main_count is not None:
                parts.append(f"main={main_count:,}")
            for name, count in member_ledgers.items():
                parts.append(f"{name}={count}")
            click.echo(" | ".join(parts))
            return

        # Full report
        color = {"SAFE": "green", "AHEAD-OF-BACKUP": "yellow", "UNKNOWN": "bright_black"}[verdict]
        click.secho(f"\n=== my-state: {verdict} ===\n", fg=color, bold=True)

        click.secho("Ledgers:", fg="cyan")
        if main_count is not None:
            click.echo(f"  main event_ledger.db       {main_count:,} events")
        else:
            click.secho("  main event_ledger.db       (not found)", fg="bright_black")
        for name, count in member_ledgers.items():
            click.echo(f"  {name}_ledger.db{' ' * max(1, 15 - len(name))}{count:,} events")
        if not member_ledgers:
            click.secho("  (no family-member ledgers)", fg="bright_black")

        click.secho("\nPersonal directories (last modified):", fg="cyan")
        for name, ts in dir_mtimes.items():
            pad = " " * max(1, 18 - len(name))
            click.echo(f"  {name}{pad}{_fmt_ago(ts)}")

        click.secho("\nExperimental backup:", fg="cyan")
        if exp is None:
            click.secho(
                "  (no experimental repo found — set DIVINEOS_EXPERIMENTAL_PATH "
                "or clone DivineOS-Experimental as a sibling directory)",
                fg="bright_black",
            )
        else:
            click.echo(f"  repo:          {exp}")
            click.echo(f"  last commit:   {_fmt_ago(exp_commit_ts)}")

        click.secho("\nVerdict:", fg=color, bold=True)
        if verdict == "SAFE":
            click.secho(
                "  Personal content is synced with experimental. Nothing to do.",
                fg="green",
            )
        elif verdict == "AHEAD-OF-BACKUP":
            click.secho(
                "  Working tree has changed since last backup. Run:\n"
                "    bash scripts/sync-to-experimental.sh\n"
                "  (or wait for the automatic phase 9g sync at session-end extract)",
                fg="yellow",
            )
        else:
            click.secho(
                "  Cannot determine safety — missing experimental repo or empty personal content.",
                fg="bright_black",
            )
        click.echo("")
