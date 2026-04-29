"""
Visual-rendering extension for iter_tool.

For matplotlib-shaped SWE-bench tasks where the failure mode is *visual* (axis
labels, tick placement, colormap, layout, legend), reading a stack trace isn't
enough — the agent needs to see what the patched code actually draws.

This module runs a small snippet of plotting code against the patched workspace,
forces matplotlib's Agg backend, captures every open figure as a PNG, and
returns the bytes as base64 strings the agent can decode and view inline.

Runs via WSL python (workspace must be pip-installed in the WSL env first).
No Docker round-trip — feedback is ~1-3s, fast enough to be interactive.

Programmatic form:
    from visual_tool import capture_plot
    result = capture_plot(
        "matplotlib__matplotlib-24149",
        plot_code='''
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots()
            ax.bar([1, 2, 3], [float("nan")] * 3)
        ''',
    )
    # result["figures"][0]["png_base64"] is the rendered PNG
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).parent.resolve()
WORKSPACES = THIS_DIR / "live_workspaces"
WSL_DISTRO = "Ubuntu"
DEFAULT_WSL_PYTHON = "/opt/swebench-env/bin/python"


def _build_wrapper(plot_code: str, dpi: int) -> str:
    return (
        "import base64, io, json, sys, traceback\n"
        "try:\n"
        "    import matplotlib\n"
        "    matplotlib.use('Agg')\n"
        "    import matplotlib.pyplot as plt\n"
        "except Exception as e:\n"
        "    print(json.dumps({'errored': True, 'stderr': f'matplotlib import failed: {e}', 'figures': []}))\n"
        "    sys.exit(0)\n"
        "_user_globals = {'__name__': '__visual_tool__'}\n"
        "_err = ''\n"
        "try:\n"
        f"    exec(compile({plot_code!r}, '<plot_code>', 'exec'), _user_globals)\n"
        "except Exception:\n"
        "    _err = traceback.format_exc()\n"
        "figures = []\n"
        "for i, num in enumerate(plt.get_fignums()):\n"
        "    fig = plt.figure(num)\n"
        "    buf = io.BytesIO()\n"
        "    try:\n"
        f"        fig.savefig(buf, format='png', bbox_inches='tight', dpi={dpi})\n"
        "    except Exception as e:\n"
        "        figures.append({'index': i, 'errored': True, 'stderr': str(e)})\n"
        "        continue\n"
        "    data = buf.getvalue()\n"
        "    figures.append({\n"
        "        'index': i,\n"
        "        'png_base64': base64.b64encode(data).decode('ascii'),\n"
        "        'size_bytes': len(data),\n"
        "    })\n"
        "print(json.dumps({'errored': bool(_err), 'stderr': _err, 'figures': figures}))\n"
    )


def _windows_to_wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def capture_plot(
    instance_id: str,
    plot_code: str,
    workspace_dir: Path | None = None,
    python_path: str = DEFAULT_WSL_PYTHON,
    dpi: int = 100,
    timeout: int = 60,
) -> dict[str, Any]:
    """Run plot_code in the workspace's WSL python, capture all open figures.

    Returns:
        {
            "errored": bool — True if exec raised
            "stderr": str — traceback if exec raised
            "figures": [{"index": int, "png_base64": str, "size_bytes": int}, ...]
            "harness_error": str — non-empty if WSL invocation itself failed
        }
    """
    if workspace_dir is None:
        workspace_dir = WORKSPACES / instance_id
    workspace_dir = Path(workspace_dir)

    if not workspace_dir.exists():
        return {
            "errored": True,
            "stderr": "",
            "figures": [],
            "harness_error": f"workspace not found: {workspace_dir}",
        }

    wrapper = _build_wrapper(plot_code, dpi)
    wsl_workspace = _windows_to_wsl_path(workspace_dir)

    wrapper_dir = THIS_DIR / "visual_runs" / "_wrappers"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = wrapper_dir / f"{instance_id.replace('/', '_')}.py"
    wrapper_path.write_text(wrapper, encoding="utf-8")
    wsl_wrapper = _windows_to_wsl_path(wrapper_path)

    try:
        completed = subprocess.run(
            [
                "wsl",
                "-d",
                WSL_DISTRO,
                "--",
                "bash",
                "-c",
                f"cd {wsl_workspace!r} && {python_path} {wsl_wrapper!r}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "errored": True,
            "stderr": "",
            "figures": [],
            "harness_error": f"timeout after {timeout}s",
        }

    stdout = completed.stdout.strip()
    if not stdout:
        return {
            "errored": True,
            "stderr": "",
            "figures": [],
            "harness_error": f"no output from wrapper. stderr: {completed.stderr[-500:]}",
        }

    last_line = stdout.splitlines()[-1]
    try:
        parsed = json.loads(last_line)
    except json.JSONDecodeError:
        return {
            "errored": True,
            "stderr": "",
            "figures": [],
            "harness_error": f"could not parse wrapper output: {stdout[-500:]}",
        }
    parsed.setdefault("harness_error", "")
    return parsed


def save_figures(result: dict[str, Any], out_dir: Path, prefix: str = "fig") -> list[Path]:
    """Decode the base64 figures and write PNGs to disk. Returns paths written."""
    import base64

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for fig in result.get("figures", []):
        if "png_base64" not in fig:
            continue
        path = out_dir / f"{prefix}_{fig['index']:02d}.png"
        path.write_bytes(base64.b64decode(fig["png_base64"]))
        paths.append(path)
    return paths


def main() -> int:
    import sys

    if len(sys.argv) < 3:
        print("usage: visual_tool.py <instance_id> <plot_code_file> [out_dir]", file=sys.stderr)
        return 2
    instance_id = sys.argv[1]
    code = Path(sys.argv[2]).read_text(encoding="utf-8")
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else THIS_DIR / "visual_runs" / instance_id

    result = capture_plot(instance_id, code)
    if result.get("harness_error"):
        print(f"[viz] harness error: {result['harness_error']}", file=sys.stderr)
        return 1
    if result.get("errored"):
        print(f"[viz] exec error:\n{result['stderr']}", file=sys.stderr)
    paths = save_figures(result, out_dir)
    print(f"[viz] captured {len(paths)} figure(s) -> {out_dir}")
    for p in paths:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
