from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stdout}")


def probe_duration(ffprobe_bin: str, path: str) -> float:
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "json",
        path,
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stdout}")
    data = json.loads(p.stdout)
    dur = float((data.get("format") or {}).get("duration") or 0.0)
    return dur


def trim_video(
    *,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    in_path: str,
    out_path: str,
    target_sec: int,
    strategy: str = "middle",   # start|middle|random
) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    total = probe_duration(ffprobe_bin, in_path)
    if total <= 0:
        raise RuntimeError(f"Cannot probe duration: {in_path}")

    t = max(1, int(target_sec))
    if total <= t:
        # 不裁剪，直接转封装/拷贝（这里直接 copy 容器）
        _run([ffmpeg_bin, "-y", "-i", in_path, "-c", "copy", out_path])
        return out_path

    if strategy == "start":
        start = 0.0
    elif strategy == "middle":
        start = max(0.0, (total - t) / 2.0)
    else:
        # random: 用一个稳定伪随机（避免额外依赖）
        import hashlib
        h = hashlib.md5(in_path.encode("utf-8")).hexdigest()
        r = int(h[:8], 16) / 0xFFFFFFFF
        start = max(0.0, (total - t) * r)

    _run([
        ffmpeg_bin, "-y",
        "-ss", f"{start:.3f}",
        "-t", str(t),
        "-i", in_path,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-an",
        out_path,
    ])
    return out_path