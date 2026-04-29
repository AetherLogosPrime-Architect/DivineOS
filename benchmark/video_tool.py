"""
Video extraction tool — keyframe sampling + audio transcription.

Companion to visual_tool.py. Takes a video file and produces:
  - sampled frames as PNG files
  - extracted audio as WAV (optional)
  - transcribed text via openai-whisper (optional)

Each frame is a separate image input. The transcript is text. There is no
continuous-video processing — the tool reduces video to discrete artifacts.

Two extraction modes:
  - "interval": one frame every N seconds (default: 2.0)
  - "scene": ffmpeg scene-change detection (threshold ~0.3)

Programmatic form:
    from video_tool import extract_video
    result = extract_video("/path/to/video.mp4", interval=1.5)
    # result["frames"] = [{"index": 0, "timestamp_sec": 0.0, "path": ...}, ...]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).parent.resolve()
WSL_DISTRO = "Ubuntu"
WSL_PYTHON = "/opt/swebench-env/bin/python"


def _windows_to_wsl_path(p: Path | str) -> str:
    s = str(p) if str(p).startswith("/mnt/") else str(Path(p).resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def _wsl(cmd: str, timeout: int = 300) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["wsl", "-d", WSL_DISTRO, "--", "bash", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


def probe_video(video_path: str | Path) -> dict[str, Any]:
    """Return duration (sec), width, height, fps, has_audio for a video."""
    wsl_video = _windows_to_wsl_path(video_path)
    cmd = f"ffprobe -v error -print_format json -show_format -show_streams {wsl_video!r}"
    rc, out, err = _wsl(cmd, timeout=30)
    if rc != 0:
        return {"error": err.strip() or "ffprobe failed"}
    data = json.loads(out)
    streams = data.get("streams", [])
    video_s = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_s = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration", 0))
    fps_str = video_s.get("avg_frame_rate", "0/1")
    try:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "duration_sec": duration,
        "width": int(video_s.get("width", 0)),
        "height": int(video_s.get("height", 0)),
        "fps": fps,
        "has_audio": audio_s is not None,
        "video_codec": video_s.get("codec_name", ""),
    }


def extract_frames(
    video_path: str | Path,
    out_dir: Path,
    mode: str = "interval",
    interval: float = 2.0,
    scene_threshold: float = 0.3,
    max_frames: int = 60,
    width: int = 800,
) -> list[dict[str, Any]]:
    """Extract sampled frames as PNGs.

    mode: "interval" (every `interval` seconds) or "scene" (scene-change).
    width: downscale to this width (preserves aspect ratio). 0 = original.
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    wsl_video = _windows_to_wsl_path(video_path)
    wsl_out = _windows_to_wsl_path(out_dir)

    scale_filter = f"scale={width}:-2," if width else ""

    if mode == "scene":
        vf = f"{scale_filter}select='gt(scene\\,{scene_threshold})'"
        cmd = (
            f"ffmpeg -hide_banner -loglevel error -y -i {wsl_video!r} "
            f"-vf {vf!r} -vsync vfr -frames:v {max_frames} "
            f"{wsl_out!r}/frame_%04d.png"
        )
    else:
        fps = 1.0 / interval
        vf = f"{scale_filter}fps={fps}"
        cmd = (
            f"ffmpeg -hide_banner -loglevel error -y -i {wsl_video!r} "
            f"-vf {vf!r} -frames:v {max_frames} "
            f"{wsl_out!r}/frame_%04d.png"
        )

    rc, out, err = _wsl(cmd, timeout=600)
    if rc != 0:
        return [{"error": err.strip() or "ffmpeg failed"}]

    frames = sorted(out_dir.glob("frame_*.png"))
    manifest: list[dict[str, Any]] = []
    for i, p in enumerate(frames):
        ts = i * interval if mode == "interval" else None
        manifest.append(
            {
                "index": i,
                "timestamp_sec": ts,
                "path": str(p),
                "size_bytes": p.stat().st_size,
            }
        )
    return manifest


def extract_audio(video_path: str | Path, out_path: Path) -> dict[str, Any]:
    """Extract the audio track to a 16kHz mono WAV (whisper-friendly)."""
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wsl_video = _windows_to_wsl_path(video_path)
    wsl_out = _windows_to_wsl_path(out_path)
    cmd = (
        f"ffmpeg -hide_banner -loglevel error -y -i {wsl_video!r} "
        f"-vn -ac 1 -ar 16000 -f wav {wsl_out!r}"
    )
    rc, out, err = _wsl(cmd, timeout=300)
    if rc != 0:
        return {"error": err.strip() or "ffmpeg audio extraction failed"}
    return {"path": str(out_path), "size_bytes": out_path.stat().st_size}


def transcribe_audio(wav_path: Path, model: str = "base") -> dict[str, Any]:
    """Transcribe via openai-whisper running in WSL.

    Requires: pip install openai-whisper in /opt/swebench-env. If not present,
    returns {"error": "whisper not installed"}.
    """
    wsl_wav = _windows_to_wsl_path(wav_path)
    script = (
        "import json, sys\n"
        "try:\n"
        "    import whisper\n"
        "except ImportError:\n"
        "    print(json.dumps({'error': 'whisper not installed'})); sys.exit(0)\n"
        f"m = whisper.load_model({model!r})\n"
        f"r = m.transcribe({wsl_wav!r}, fp16=False)\n"
        "out = {'text': r['text'], 'segments': [\n"
        "    {'start': s['start'], 'end': s['end'], 'text': s['text']}\n"
        "    for s in r.get('segments', [])\n"
        "]}\n"
        "print(json.dumps(out))\n"
    )
    wrapper_dir = THIS_DIR / "video_runs" / "_wrappers"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper = wrapper_dir / "transcribe.py"
    wrapper.write_text(script, encoding="utf-8")
    wsl_wrapper = _windows_to_wsl_path(wrapper)

    rc, out, err = _wsl(f"{WSL_PYTHON} {wsl_wrapper!r}", timeout=1800)
    if rc != 0:
        return {"error": (err or out)[-500:]}
    last = out.strip().splitlines()[-1] if out.strip() else ""
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return {"error": f"could not parse: {out[-500:]}"}


def _frame_delta(path_a: str, path_b: str, downsample: int = 64) -> float:
    """Mean-abs pixel difference between two frames at low resolution.

    Returns a float in [0, 255]. Higher = more visual change between frames.
    Downsamples both before comparing — cheap and stable enough for picking
    which gaps need more frames.
    """
    from PIL import Image

    a = Image.open(path_a).convert("L").resize((downsample, downsample))
    b = Image.open(path_b).convert("L").resize((downsample, downsample))
    pa = list(a.getdata())
    pb = list(b.getdata())
    return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)


def bisect_refine(
    video_path: str | Path,
    frames: list[dict[str, Any]],
    out_dir: Path,
    top_k: int | None = None,
    min_delta: float = 0.0,
    width: int = 800,
) -> list[dict[str, Any]]:
    """Add midpoint frames where adjacent frames differ most.

    For each adjacent pair in `frames` (sorted by timestamp), compute the
    visual delta. Pick the top_k highest-delta pairs (or all above min_delta),
    extract a frame at each pair's midpoint timestamp, return merged manifest.

    Call repeatedly to refine further — each call doubles resolution where
    motion is densest, leaving stable regions sparse.
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    sorted_frames = sorted(
        [f for f in frames if f.get("timestamp_sec") is not None and f.get("path")],
        key=lambda f: f["timestamp_sec"],
    )
    if len(sorted_frames) < 2:
        return frames

    deltas = []
    for i in range(len(sorted_frames) - 1):
        a, b = sorted_frames[i], sorted_frames[i + 1]
        d = _frame_delta(a["path"], b["path"])
        deltas.append((d, a, b))

    candidates = [(d, a, b) for d, a, b in deltas if d >= min_delta]
    candidates.sort(key=lambda x: x[0], reverse=True)
    if top_k is not None:
        candidates = candidates[:top_k]

    new_frames = []
    for d, a, b in candidates:
        midpoint = (a["timestamp_sec"] + b["timestamp_sec"]) / 2
        wsl_video = _windows_to_wsl_path(video_path)
        out_name = f"frame_mid_{midpoint:09.3f}.png".replace(".", "p", 1)
        out_path = out_dir / out_name
        wsl_out = _windows_to_wsl_path(out_path)
        scale_filter = f"scale={width}:-2" if width else "null"
        cmd = (
            f"ffmpeg -hide_banner -loglevel error -y -ss {midpoint} -i {wsl_video!r} "
            f"-vf {scale_filter!r} -frames:v 1 {wsl_out!r}"
        )
        rc, _, err = _wsl(cmd, timeout=60)
        if rc != 0 or not out_path.exists():
            continue
        new_frames.append(
            {
                "index": -1,
                "timestamp_sec": midpoint,
                "path": str(out_path),
                "size_bytes": out_path.stat().st_size,
                "delta_to_neighbors": d,
                "from_bisect": True,
            }
        )

    merged = sorted(
        sorted_frames + new_frames,
        key=lambda f: f["timestamp_sec"],
    )
    for i, f in enumerate(merged):
        f["index"] = i
    return merged


def extract_video(
    video_path: str | Path,
    out_dir: Path | None = None,
    mode: str = "interval",
    interval: float = 2.0,
    max_frames: int = 60,
    width: int = 800,
    audio: bool = False,
    transcribe: bool = False,
    whisper_model: str = "base",
) -> dict[str, Any]:
    """Full extraction: probe + frames + (optional) audio + (optional) transcript.

    Returns a dict with probe, frames manifest, audio info, transcript, out_dir.
    """
    if out_dir is None:
        stem = Path(str(video_path)).stem
        out_dir = THIS_DIR / "video_runs" / stem
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    probe = probe_video(video_path)
    frames = extract_frames(
        video_path,
        out_dir / "frames",
        mode=mode,
        interval=interval,
        max_frames=max_frames,
        width=width,
    )

    audio_info = None
    transcript_info = None
    if audio or transcribe:
        wav_path = out_dir / "audio.wav"
        audio_info = extract_audio(video_path, wav_path)
        if transcribe and "error" not in audio_info:
            transcript_info = transcribe_audio(wav_path, model=whisper_model)

    return {
        "probe": probe,
        "frames": frames,
        "audio": audio_info,
        "transcript": transcript_info,
        "out_dir": str(out_dir),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: video_tool.py <video_path> [interval_sec] [max_frames]", file=sys.stderr)
        return 2
    video = sys.argv[1]
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    max_frames = int(sys.argv[3]) if len(sys.argv) > 3 else 60

    result = extract_video(video, interval=interval, max_frames=max_frames)
    print(f"[video] probe: {result['probe']}")
    print(f"[video] frames: {len(result['frames'])} -> {result['out_dir']}/frames/")
    for f in result["frames"][:5]:
        print(f"  {f.get('timestamp_sec'):>6}s  {f.get('path')}")
    if len(result["frames"]) > 5:
        print(f"  ... +{len(result['frames']) - 5} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
