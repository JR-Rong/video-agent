from __future__ import annotations

import base64
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from ..utils.files import ensure_dir
from ..utils.text import slugify
from ..providers.tts_edge import tts_to_mp3
from ..providers.video_ffmpeg import (
    image_to_motion_clip,
    normalize_video_clip,
    concat_clips,
    mux_audio,
)
from ..library import MediaLibrary, match_media, tokenize_keywords

log = logging.getLogger(__name__)


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_export_path(
    *,
    output_routing_path: str,
    export_root_default: str,
    category: str,
    series: str,
    episode: int,
) -> str:
    cfg = _load_yaml(output_routing_path)
    export_root = (cfg.get("export_root") or export_root_default) or export_root_default
    routes = cfg.get("routes") or {}
    sub = routes.get(category) or routes.get("default") or "general"
    safe_series = slugify(series)
    return os.path.join(export_root, sub, safe_series, f"ep_{episode}")


def _scene_query_keywords(scene: dict[str, Any]) -> list[str]:
    return tokenize_keywords(
        " ".join(
            [
                str(scene.get("on_screen_text") or ""),
                str(scene.get("narration") or ""),
                str(scene.get("image_prompt") or ""),
                str(scene.get("video_prompt") or ""),
            ]
        )
    )


def _pick_dims_from_importance(providers_cfg: dict[str, Any], importance: str) -> tuple[int, int, int]:
    vg = providers_cfg.get("video_generation") or {}
    if (importance or "").lower() == "key":
        ks = vg.get("key_shot") or {}
        return int(ks.get("width") or 1080), int(ks.get("height") or 1920), int(ks.get("fps") or 24)
    df = vg.get("default") or {}
    return int(df.get("width") or 720), int(df.get("height") or 1280), int(df.get("fps") or 24)


def _file_to_base64(path: str) -> str:
    b = Path(path).read_bytes()
    return base64.b64encode(b).decode("utf-8")


def render_media_video(
    *,
    run_dir: str,
    ffmpeg_bin: str,
    render_presets_path: str,
    preset_name: str,
    providers_config_path: str,
    script: dict[str, Any],
    images: Any,  # ImagesRouter: must provide generate_image(...)
    video_generator: Any | None,  # must provide generate_clip(...)
    library: MediaLibrary,
    total_seconds: int,
    media_mode: str,  # images|videos|mixed
    tts_voice: str,
    reuse_min_score: float = 78.0,
    tracer: Any | None = None,
) -> tuple[dict[str, Any], str]:
    presets = _load_yaml(render_presets_path).get("presets") or {}
    preset = presets.get(preset_name)
    if not preset:
        raise RuntimeError(f"Unknown render preset: {preset_name}. Available: {list(presets.keys())}")

    # final output canvas/fps
    out_w = int(preset["width"])
    out_h = int(preset["height"])
    out_fps = int(preset["fps"])

    providers_cfg = _load_yaml(providers_config_path)

    ensure_dir(run_dir)
    scenes = script.get("scenes") or []
    if not scenes:
        raise RuntimeError("render_media_video: script.scenes is empty")

    log.info(
        "Render start: run_dir=%s preset=%s out=%sx%s@%sfps media_mode=%s total_seconds=%s scenes=%s reuse_min_score=%.1f",
        run_dir,
        preset_name,
        out_w,
        out_h,
        out_fps,
        media_mode,
        total_seconds,
        len(scenes),
        reuse_min_score,
    )

    # allocate to match total_seconds
    allocations = [max(1, int(s.get("seconds", 1))) for s in scenes]
    ssum = sum(allocations)
    if ssum != total_seconds:
        allocations[-1] = max(1, allocations[-1] + (total_seconds - ssum))

    clip_paths: list[str] = []
    scene_assets: list[dict[str, Any]] = []

    for scene, sec in zip(scenes, allocations):
        sid = int(scene["id"])
        scene_kw = _scene_query_keywords(scene)

        # decide media type by mode
        media_type = (scene.get("media_type") or "image").lower()
        if media_mode == "images":
            media_type = "image"
        elif media_mode == "videos":
            media_type = "video"
        elif media_mode == "mixed":
            media_type = "video" if media_type == "video" else "image"
        else:
            raise ValueError("media_mode must be one of: images, videos, mixed")

        orientation = (scene.get("orientation") or "portrait").lower()
        importance = (scene.get("importance") or "normal").lower()

        # generation target (for marking to library & for video generation size)
        gen_w, gen_h, gen_fps = _pick_dims_from_importance(providers_cfg, importance)
        if orientation == "landscape":
            gen_w, gen_h = max(gen_w, gen_h), min(gen_w, gen_h)
        else:
            gen_w, gen_h = min(gen_w, gen_h), max(gen_w, gen_h)

        log.info(
            "Scene %03d start: media_type=%s secs=%s orientation=%s importance=%s gen=%sx%s@%sfps",
            sid,
            media_type,
            sec,
            orientation,
            importance,
            gen_w,
            gen_h,
            gen_fps,
        )

        # ---------- IMAGE scene ----------
        if media_type == "image":
            candidates = library.list_items(media_type="image", orientation=orientation)
            m = match_media(
                query_keywords=scene_kw,
                query_prompt=scene.get("image_prompt") or "",
                candidates=candidates,
                min_score=reuse_min_score,
            )

            if m:
                library.increment_usage(m.item.id or 0)
                img_path = m.item.file_path
                source = {"reuse": True, "match_score": m.score, "library_id": m.item.id}
                log.info("Scene %03d image reuse: library_id=%s score=%.1f path=%s", sid, m.item.id, m.score, img_path)
            else:
                img_path = os.path.join(run_dir, f"scene_{sid:03d}.png")
                log.info("Scene %03d image generate: %s", sid, img_path)
                images.generate_image(
                    prompt=scene.get("image_prompt") or "",
                    out_path=img_path,
                    negative_prompt=scene.get("negative_prompt"),
                    orientation=orientation,
                    importance=importance,
                    tracer=tracer,
                    step="scene_image",
                    scene_id=sid,
                )
                item = library.add_item(
                    media_type="image",
                    src_file_path=img_path,
                    keywords=scene_kw,
                    prompt=scene.get("image_prompt") or "",
                    negative_prompt=scene.get("negative_prompt"),
                    width=gen_w,
                    height=gen_h,
                    orientation=orientation,
                )
                source = {"reuse": False, "library_id": item.id}
                log.info("Scene %03d image saved to library: id=%s", sid, item.id)

            clip_path = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
            log.info("Scene %03d image->motion clip: %s", sid, clip_path)
            image_to_motion_clip(
                ffmpeg_bin=ffmpeg_bin,
                image_path=img_path,
                seconds=sec,
                out_path=clip_path,
                width=out_w,
                height=out_h,
                fps=out_fps,
            )
            if not os.path.exists(clip_path):
                raise RuntimeError(f"Scene {sid} clip not created: {clip_path}")

            clip_paths.append(clip_path)
            scene_assets.append(
                {
                    "id": sid,
                    "type": "image_motion",
                    "orientation": orientation,
                    "importance": importance,
                    "image": img_path,
                    "clip": clip_path,
                    "seconds": sec,
                    **source,
                }
            )
            continue

        # ---------- VIDEO scene ----------
        if media_type == "video":
            if not video_generator:
                raise RuntimeError("video_generation provider not configured but media_mode requires video.")

            # 1) try reuse video first
            v_candidates = library.list_items(media_type="video", orientation=orientation)
            mv = match_media(
                query_keywords=scene_kw,
                query_prompt=scene.get("video_prompt") or "",
                candidates=v_candidates,
                min_score=reuse_min_score,
            )

            if mv:
                library.increment_usage(mv.item.id or 0)
                raw_clip = mv.item.file_path
                v_source = {"reuse": True, "match_score": mv.score, "library_id": mv.item.id, "method": "reuse_video"}
                log.info("Scene %03d video reuse: library_id=%s score=%.1f path=%s", sid, mv.item.id, mv.score, raw_clip)
            else:
                # 2) Prepare reference image (prefer reuse image; else generate new)
                i_candidates = library.list_items(media_type="image", orientation=orientation)
                mi = match_media(
                    query_keywords=scene_kw,
                    query_prompt=scene.get("image_prompt") or "",
                    candidates=i_candidates,
                    min_score=reuse_min_score,
                )

                if mi:
                    library.increment_usage(mi.item.id or 0)
                    ref_img_path = mi.item.file_path
                    i_source = {
                        "ref_image_reuse": True,
                        "ref_image_library_id": mi.item.id,
                        "ref_image_match_score": mi.score,
                    }
                    log.info(
                        "Scene %03d ref image reuse: library_id=%s score=%.1f path=%s",
                        sid,
                        mi.item.id,
                        mi.score,
                        ref_img_path,
                    )
                else:
                    ref_img_path = os.path.join(run_dir, f"ref_{sid:03d}.png")
                    log.info("Scene %03d ref image generate: %s", sid, ref_img_path)
                    images.generate_image(
                        prompt=scene.get("image_prompt") or "",
                        out_path=ref_img_path,
                        negative_prompt=scene.get("negative_prompt"),
                        orientation=orientation,
                        importance=importance,
                        tracer=tracer,
                        step="scene_ref_image",
                        scene_id=sid,
                    )
                    it = library.add_item(
                        media_type="image",
                        src_file_path=ref_img_path,
                        keywords=scene_kw,
                        prompt=scene.get("image_prompt") or "",
                        negative_prompt=scene.get("negative_prompt"),
                        width=gen_w,
                        height=gen_h,
                        orientation=orientation,
                    )
                    i_source = {"ref_image_reuse": False, "ref_image_library_id": it.id}
                    log.info("Scene %03d ref image saved to library: id=%s", sid, it.id)

                # 3) generate video clip using image2video preference (reference_image_base64)
                ref_b64 = _file_to_base64(ref_img_path)
                raw_clip = os.path.join(run_dir, f"rawclip_{sid:03d}.mp4")
                log.info("Scene %03d video generate (image2video preferred): raw_clip=%s", sid, raw_clip)

                video_generator.generate_clip(
                    prompt=scene.get("video_prompt") or scene.get("image_prompt") or "",
                    negative_prompt=scene.get("negative_prompt"),
                    seconds=sec,
                    out_path=raw_clip,
                    width=gen_w,
                    height=gen_h,
                    fps=gen_fps,
                    reference_image_base64=ref_b64,
                )
                if not os.path.exists(raw_clip):
                    raise RuntimeError(f"Scene {sid} raw clip not created: {raw_clip}")

                vit = library.add_item(
                    media_type="video",
                    src_file_path=raw_clip,
                    keywords=scene_kw,
                    prompt=scene.get("video_prompt") or "",
                    negative_prompt=scene.get("negative_prompt"),
                    width=gen_w,
                    height=gen_h,
                    seconds=sec,
                    orientation=orientation,
                )
                v_source = {"reuse": False, "library_id": vit.id, "method": "image2video", **i_source}
                log.info("Scene %03d video saved to library: id=%s", sid, vit.id)

            # normalize for final concat
            norm_clip = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
            log.info("Scene %03d normalize clip: %s", sid, norm_clip)
            normalize_video_clip(
                ffmpeg_bin=ffmpeg_bin,
                in_path=raw_clip,
                out_path=norm_clip,
                width=out_w,
                height=out_h,
                fps=out_fps,
            )
            if not os.path.exists(norm_clip):
                raise RuntimeError(f"Scene {sid} normalized clip not created: {norm_clip}")

            clip_paths.append(norm_clip)
            scene_assets.append(
                {
                    "id": sid,
                    "type": "video",
                    "orientation": orientation,
                    "importance": importance,
                    "raw_clip": raw_clip,
                    "clip": norm_clip,
                    "seconds": sec,
                    **v_source,
                }
            )
            continue

        raise ValueError(f"Unknown media_type: {media_type}")

    # narration
    narration_text = "\n".join([s.get("narration", "") for s in scenes]).strip()
    audio_path = os.path.join(run_dir, "narration.mp3")
    log.info("TTS start: voice=%s -> %s", tts_voice, audio_path)
    tts_to_mp3(text=narration_text, out_path=audio_path, voice=tts_voice)
    if not os.path.exists(audio_path):
        raise RuntimeError(f"TTS failed: {audio_path}")

    # concat
    concat_path = os.path.join(run_dir, "concat.mp4")
    log.info("Concat start: %d clips -> %s", len(clip_paths), concat_path)
    # extra debug: ensure all clips exist
    missing = [p for p in clip_paths if not os.path.exists(p)]
    if missing:
        raise RuntimeError(f"Missing clips before concat: {missing}")
    concat_clips(ffmpeg_bin=ffmpeg_bin, clip_paths=clip_paths, out_path=concat_path)
    if not os.path.exists(concat_path):
        raise RuntimeError(f"Concat failed: {concat_path}")

    # mux audio
    final_path = os.path.join(run_dir, "final.mp4")
    log.info("Mux start: video=%s audio=%s -> %s", concat_path, audio_path, final_path)
    mux_audio(ffmpeg_bin=ffmpeg_bin, video_path=concat_path, audio_path=audio_path, out_path=final_path)
    if not os.path.exists(final_path):
        raise RuntimeError(f"Mux failed: {final_path}")

    log.info("Render done: final=%s", final_path)

    assets = {
        "run_dir": run_dir,
        "preset": preset_name,
        "out_width": out_w,
        "out_height": out_h,
        "out_fps": out_fps,
        "audio_path": audio_path,
        "concat_path": concat_path,
        "scene_assets": scene_assets,
        "total_seconds": total_seconds,
        "media_mode": media_mode,
        "reuse_min_score": reuse_min_score,
        "clip_paths": clip_paths,
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
    tracer: Any | None = None, 
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

    log.info("Export done: %s", export_dir)
    return export_dir