from __future__ import annotations

import os
import shutil
from typing import Any

import yaml

from ..utils.files import ensure_dir
from ..utils.text import slugify
from ..providers.images_openai import OpenAIImages
from ..providers.tts_edge import tts_to_mp3
from ..providers.video_ffmpeg import (
    image_to_motion_clip,
    normalize_video_clip,
    concat_clips,
    mux_audio,
)
from ..providers.video_clips_openai import OpenAIVideoClips


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_export_path(*, output_routing_path: str, export_root_default: str, category: str, series: str, episode: int) -> str:
    cfg = _load_yaml(output_routing_path)
    export_root = (cfg.get("export_root") or export_root_default) or export_root_default
    routes = cfg.get("routes") or {}
    sub = routes.get(category) or routes.get("default") or "general"

    safe_series = slugify(series)
    return os.path.join(export_root, sub, safe_series, f"ep_{episode}")


def render_media_video(
    *,
    run_dir: str,
    ffmpeg_bin: str,
    render_presets_path: str,
    preset_name: str,
    script: dict[str, Any],
    images: OpenAIImages,
    video_clips: OpenAIVideoClips | None,
    total_seconds: int,
    media_mode: str,  # images|videos|mixed
    tts_voice: str,
) -> tuple[dict[str, Any], str]:
    presets = _load_yaml(render_presets_path).get("presets") or {}
    preset = presets.get(preset_name)
    if not preset:
        raise RuntimeError(f"Unknown render preset: {preset_name}. Available: {list(presets.keys())}")

    width = int(preset["width"])
    height = int(preset["height"])
    fps = int(preset["fps"])

    ensure_dir(run_dir)

    scenes = script["scenes"]

    # Allocate durations to match total_seconds
    allocations = [max(1, int(s.get("seconds", 1))) for s in scenes]
    ssum = sum(allocations)
    if ssum != total_seconds:
        allocations[-1] = max(1, allocations[-1] + (total_seconds - ssum))

    clip_paths: list[str] = []
    scene_assets: list[dict[str, Any]] = []

    for scene, sec in zip(scenes, allocations):
        sid = int(scene["id"])
        media_type = (scene.get("media_type") or "image").lower()

        # user override by global mode
        if media_mode == "images":
            media_type = "image"
        elif media_mode == "videos":
            media_type = "video"
        elif media_mode == "mixed":
            media_type = "video" if media_type == "video" else "image"
        else:
            raise ValueError("media_mode must be one of: images, videos, mixed")

        if media_type == "image":
            img_path = os.path.join(run_dir, f"scene_{sid:03d}.png")
            images.generate_image(prompt=scene["image_prompt"], out_path=img_path, size="1024x1024")

            clip_path = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
            image_to_motion_clip(
                ffmpeg_bin=ffmpeg_bin,
                image_path=img_path,
                seconds=sec,
                out_path=clip_path,
                width=width,
                height=height,
                fps=fps,
            )
            clip_paths.append(clip_path)
            scene_assets.append({"id": sid, "type": "image_motion", "image": img_path, "clip": clip_path, "seconds": sec})

        elif media_type == "video":
            if not video_clips:
                raise RuntimeError("video_clips provider not configured.")
            raw_clip = os.path.join(run_dir, f"rawclip_{sid:03d}.mp4")
            # 生成片段（你后续补齐 OpenAIVideoClips.generate_clip）
            video_clips.generate_clip(prompt=scene.get("video_prompt") or scene["image_prompt"], seconds=sec, out_path=raw_clip)

            norm_clip = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
            normalize_video_clip(
                ffmpeg_bin=ffmpeg_bin,
                in_path=raw_clip,
                out_path=norm_clip,
                width=width,
                height=height,
                fps=fps,
            )
            clip_paths.append(norm_clip)
            scene_assets.append({"id": sid, "type": "video", "raw_clip": raw_clip, "clip": norm_clip, "seconds": sec})
        else:
            raise ValueError(f"Unknown media_type: {media_type}")

    narration_text = "\n".join([s.get("narration", "") for s in scenes]).strip()
    audio_path = os.path.join(run_dir, "narration.mp3")
    tts_to_mp3(text=narration_text, out_path=audio_path, voice=tts_voice)

    # concat then mux audio
    concat_path = os.path.join(run_dir, "concat.mp4")
    concat_clips(ffmpeg_bin=ffmpeg_bin, clip_paths=clip_paths, out_path=concat_path)

    final_path = os.path.join(run_dir, "final.mp4")
    mux_audio(ffmpeg_bin=ffmpeg_bin, video_path=concat_path, audio_path=audio_path, out_path=final_path)

    assets = {
        "run_dir": run_dir,
        "preset": preset_name,
        "width": width,
        "height": height,
        "fps": fps,
        "audio_path": audio_path,
        "concat_path": concat_path,
        "scene_assets": scene_assets,
        "total_seconds": total_seconds,
        "media_mode": media_mode,
    }
    return assets, final_path


def export_final(
    *,
    output_routing_path: str,
    export_root_default: str,
    category: str,
    series: str,
    episode: int,
    final_video_path: str,
    script: dict[str, Any],
    outline: dict[str, Any],
) -> str:
    export_dir = _resolve_export_path(
        output_routing_path=output_routing_path,
        export_root_default=export_root_default,
        category=category,
        series=series,
        episode=episode,
    )
    ensure_dir(export_dir)

    dst_video = os.path.join(export_dir, "final.mp4")
    shutil.copy2(final_video_path, dst_video)

    # 同时导出元数据，方便你手工发布时复制标题/文案
    meta_path = os.path.join(export_dir, "meta.yaml")
    meta = {
        "category": category,
        "series": series,
        "episode": episode,
        "title": script.get("platform_title") or script.get("title") or outline.get("title"),
        "description": script.get("platform_desc") or "",
        "tags": script.get("tags") or [],
        "hook": outline.get("hook"),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)

    return export_dir