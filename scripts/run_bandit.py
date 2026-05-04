#!/usr/bin/env python3
"""Run bandit security scan and summarize findings.

Usage:
    python scripts/run_bandit.py           # Medium+ severity (default)
    python scripts/run_bandit.py --all     # All severities
    python scripts/run_bandit.py --strict  # Fail on any finding
    python scripts/run_bandit.py --json    # Emit JSON report

Audit r9-21 #28: rewired to read JSON output and filter programmatically
so Windows console encoding (cp1252) doesn't crash the txt formatter on
non-ASCII rationale text. Strict mode now reports the actual finding
count cleanly instead of bubbling up a UnicodeEncodeError.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    args = sys.argv[1:]
    show_all = "--all" in args
    strict = "--strict" in args
    emit_json = "--json" in args

    # Use JSON output format so the formatter survives non-ASCII in
    # source comments. Skip B101 (assert_used) since tests use asserts.
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        cmd = [
            sys.executable,
            "-m",
            "bandit",
            "-r",
            "src/divineos/",
            "-f",
            "json",
            "-o",
            str(tmp_path),
            "-s",
            "B101",
            "--exclude",
            "tests",
        ]
        # Run silently; JSON file holds the report.
        subprocess.run(cmd, capture_output=True, check=False)

        report = json.loads(tmp_path.read_text(encoding="utf-8"))
    finally:
        tmp_path.unlink(missing_ok=True)

    results = report.get("results", [])
    if not show_all:
        # MEDIUM+ severity (the previous -ll filter)
        results = [r for r in results if r.get("issue_severity") in ("MEDIUM", "HIGH")]

    if emit_json:
        print(json.dumps({"results": results, "total": len(results)}, indent=2))
        return 0

    if not results:
        print("[bandit] no findings.")
        return 0

    by_severity: dict[str, int] = {}
    for r in results:
        sev = r.get("issue_severity", "UNKNOWN")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    print(f"[bandit] {len(results)} finding(s):")
    for sev in ("HIGH", "MEDIUM", "LOW"):
        if sev in by_severity:
            print(f"  {sev}: {by_severity[sev]}")
    print()
    for r in results[:30]:
        loc = f"{r.get('filename', '?')}:{r.get('line_number', '?')}"
        msg = r.get("issue_text", "")
        print(f"  [{r.get('test_id', '?')}] {r.get('issue_severity', '?')} {loc}")
        print(f"      {msg}")
    if len(results) > 30:
        print(f"  ... and {len(results) - 30} more")

    if strict:
        print("\n[!] Bandit found issues -- strict mode, failing.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
