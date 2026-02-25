from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence


def run(cmd: Sequence[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stdout}")


def image_to_motion_clip(
    *,
    ffmpeg_bin: str,
    image_path: str,
    seconds: int,
    out_path: str,
    width: int,
    height: int,
    fps: int,
    zoom: float = 1.10,
) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, seconds * fps)

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"zoompan=z='min(zoom+0.0008,{zoom})':"
        f"x='iw/2-(iw/zoom/2)+sin(on/30)*10':"
        f"y='ih/2-(ih/zoom/2)+cos(on/40)*10':"
        f"d={frames}:s={width}x{height},"
        f"fps={fps}"
    )

    run(
        [
            ffmpeg_bin,
            "-y",
            "-loop",
            "1",
            "-i",
            image_path,
            "-t",
            str(seconds),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            out_path,
        ]
    )
    return out_path


def normalize_video_clip(
    *,
    ffmpeg_bin: str,
    in_path: str,
    out_path: str,
    width: int,
    height: int,
    fps: int,
) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps}"
    )
    run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            in_path,
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            out_path,
        ]
    )
    return out_path


def _ffmpeg_concat_quote(path: str) -> str:
    # ffmpeg concat list single-quote escaping
    return path.replace("'", "'\\''")


def concat_clips(*, ffmpeg_bin: str, clip_paths: list[str], out_path: str) -> str:
    """
    Use concat demuxer. IMPORTANT:
    - write ABSOLUTE paths to avoid ffmpeg resolving relative paths against concat file directory.
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    list_file = str(Path(out_path).with_suffix(".concat.txt"))

    lines = []
    for p in clip_paths:
        ap = os.path.abspath(p)
        qp = _ffmpeg_concat_quote(ap)
        lines.append(f"file '{qp}'")

    Path(list_file).write_text("\n".join(lines), encoding="utf-8")

    run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_file,
            "-c",
            "copy",
            out_path,
        ]
    )
    Path(list_file).unlink(missing_ok=True)
    return out_path


def mux_audio(*, ffmpeg_bin: str, video_path: str, audio_path: str, out_path: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            out_path,
        ]
    )
    return out_path