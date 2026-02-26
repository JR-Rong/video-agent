from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
from collections import deque
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz

from ..utils.files import ensure_dir
from ..utils.text import slugify
from ..providers.tts_edge import tts_to_mp3
from ..providers.video_ffmpeg import image_to_motion_clip, normalize_video_clip, concat_clips, mux_audio
from ..providers.video_trim_ffmpeg import trim_video
from ..library import MediaLibrary, match_media, tokenize_keywords

from ..providers.external_media.era_guard import OpenAICompatVisionJudge, EraGuardRouter
from ..utils.templating import load_text, render_template

log = logging.getLogger(__name__)

ERA_GUARD_SCHEMA = """{
  "ok": true,
  "confidence": 0.0,
  "reasons": ["string"],
  "detected": ["string"]
}"""


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


def _orientation_str(orientation: str) -> str:
    return "portrait" if orientation == "portrait" else "landscape"


def _build_query(scene: dict[str, Any], *, search_keywords_en: str, category: str | None) -> str:
    base = (scene.get("on_screen_text") or scene.get("narration") or scene.get("video_prompt") or scene.get("image_prompt") or "").strip()
    base = base.replace("\n", " ")
    base = base[:120] if len(base) > 120 else base
    if search_keywords_en:
        return (search_keywords_en + " " + base).strip()
    return base or ("history b-roll" if category == "history" else "cinematic b-roll")


def _pick_desired(scene: dict[str, Any], media_mode: str) -> str:
    desired = (scene.get("media_type") or "image").lower()
    if media_mode == "images":
        return "image"
    if media_mode == "videos":
        return "video"
    if media_mode == "mixed":
        return "video" if desired == "video" else "image"
    raise ValueError("media_mode must be one of: images, videos, mixed")


def _ffmpeg_extract_frame(ffmpeg_bin: str, video_path: str, out_image_path: str, ts_sec: float = 1.0) -> str:
    Path(out_image_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin, "-y",
        "-ss", str(ts_sec),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        out_image_path,
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg extract frame failed: {p.stdout}")
    return out_image_path


def _external_text_score(*, cand_page_url: str, cand_download_url: str, query: str, required_terms: list[str], banned_terms: list[str]) -> tuple[float, list[str]]:
    """
    Strong matching:
    - score based on fuzz match between query and urls
    - must contain >= min_required_terms among required_terms (in either url text)
    - must NOT contain banned_terms
    Returns: (score, reasons)
    """
    reasons: list[str] = []
    text = f"{cand_page_url} {cand_download_url}".lower()
    q = (query or "").lower()

    # banned
    hits = [t for t in banned_terms if t and t.lower() in text]
    if hits:
        return 0.0, [f"banned_terms_hit={hits[:6]}"]

    # required term hits
    req_hits = [t for t in required_terms if t and t.lower() in text]
    reasons.append(f"required_hits={req_hits[:6]}")

    # similarity
    s1 = float(fuzz.token_set_ratio(q, cand_page_url or ""))
    s2 = float(fuzz.token_set_ratio(q, cand_download_url or ""))
    score = max(s1, s2)
    reasons.append(f"url_sim={score:.1f}")
    return score, reasons


def _required_terms_from_search_keywords(search_keywords_en: str) -> list[str]:
    # simple: split by spaces, keep informative tokens (len>=3)
    toks = [t.strip().lower() for t in (search_keywords_en or "").replace(",", " ").split() if t.strip()]
    out = []
    for t in toks:
        if len(t) < 3:
            continue
        if t in ("ancient", "history", "traditional", "architecture"):
            # too generic; still allow but lower priority
            continue
        out.append(t)
    return out[:12]


def render_media_video(
    *,
    run_dir: str,
    ffmpeg_bin: str,
    render_presets_path: str,
    preset_name: str,
    providers_config_path: str,
    script: dict[str, Any],
    images: Any,
    video_generator: Any | None,
    library: MediaLibrary,
    total_seconds: int,
    media_mode: str,
    tts_voice: str,
    reuse_min_score: float = 78.0,
    tracer: Any | None = None,
    external_media: Any | None = None,
    category: str | None = None,
    search_keywords_en: str = "",
    reuse_cooldown_scenes: int = 2,
    ffprobe_bin: str = "ffprobe",
) -> tuple[dict[str, Any], str]:
    presets = _load_yaml(render_presets_path).get("presets") or {}
    preset = presets.get(preset_name)
    if not preset:
        raise RuntimeError(f"Unknown render preset: {preset_name}. Available: {list(presets.keys())}")

    out_w = int(preset["width"])
    out_h = int(preset["height"])
    out_fps = int(preset["fps"])

    providers_cfg = _load_yaml(providers_config_path)
    external_cfg = providers_cfg.get("external_search") or {}
    trim_strategy = str(external_cfg.get("trim_strategy") or "random")
    min_dur = int(external_cfg.get("min_duration_sec") or 3)
    max_dur = int(external_cfg.get("max_duration_sec") or 20)

    strict_mode = bool(external_cfg.get("strict_mode", False))
    min_match_score = float(external_cfg.get("min_match_score") or 85)
    min_required_terms = int(external_cfg.get("min_required_terms") or 2)
    banned_terms = [str(x).lower() for x in (external_cfg.get("banned_terms") or [])]

    era_guard_cfg = external_cfg.get("era_guard") or {}
    era_guard_enabled = bool(era_guard_cfg.get("enabled", False)) and str(era_guard_cfg.get("provider") or "off") != "off"
    era_guard_prompt_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "prompts", "era_media_guard.md")
    # above path is a bit ugly; better: pass prompts_dir. But keep minimal changes:
    # We'll fallback to relative: ./prompts/era_media_guard.md
    if not os.path.exists("./prompts/era_media_guard.md"):
        # if running from installed package, locate near cwd might fail; you can adjust as needed
        era_guard_prompt_path = "./prompts/era_media_guard.md"

    openai_api_key = os.getenv("OPENAI_API_KEY", "") or ""
    openai_base_url = (os.getenv("OPENAI_BASE_URL", "") or "").strip()
    judge_base_url = (era_guard_cfg.get("base_url") or openai_base_url or "").strip()
    judge_model = str(era_guard_cfg.get("model") or "gpt-4o-mini")
    judge_timeout = int(era_guard_cfg.get("timeout_sec") or 60)
    allowed_people_hint = str(era_guard_cfg.get("allowed_people_hint") or "")
    hard_block_objects = era_guard_cfg.get("hard_block_objects") or []

    vision_judge = None
    era_guard_cfg = external_cfg.get("era_guard") or {}
    era_guard_enabled = bool(era_guard_cfg.get("enabled", False)) and str(era_guard_cfg.get("provider") or "off") != "off"

    # prompt path
    era_guard_prompt_path = "./prompts/era_media_guard.md"

    # keys from env
    qwen_api_key = os.getenv("QWEN_API_KEY", "") or ""
    openai_api_key = os.getenv("OPENAI_API_KEY", "") or ""

    # base urls / models
    qwen_base_url = str(era_guard_cfg.get("qwen_base_url") or os.getenv("QWEN_BASE_URL", "") or "").strip()
    qwen_model = str(era_guard_cfg.get("qwen_model") or "qwen-vl-plus").strip()

    openai_base_url = str(era_guard_cfg.get("openai_base_url") or os.getenv("OPENAI_BASE_URL", "") or "").strip()
    openai_model = str(era_guard_cfg.get("openai_model") or "gpt-4o-mini").strip()

    judge_timeout = int(era_guard_cfg.get("timeout_sec") or 60)
    allowed_people_hint = str(era_guard_cfg.get("allowed_people_hint") or "")
    hard_block_objects = era_guard_cfg.get("hard_block_objects") or []

    # build judges according to provider preference
    provider = str(era_guard_cfg.get("provider") or "qwen_compat").strip()
    fallback_provider = str(era_guard_cfg.get("fallback_provider") or "openai_compat").strip()

    primary_judge = None
    fallback_judge = None

    if era_guard_enabled:
        if provider == "qwen_compat":
            if qwen_api_key and qwen_base_url:
                primary_judge = OpenAICompatVisionJudge(
                    base_url=qwen_base_url,
                    api_key=qwen_api_key,
                    model=qwen_model,
                    timeout_sec=judge_timeout,
                )
            else:
                log.warning("era_guard primary=qwen_compat but missing QWEN_API_KEY or qwen_base_url/QWEN_BASE_URL")
        elif provider == "openai_compat":
            if openai_api_key and openai_base_url:
                primary_judge = OpenAICompatVisionJudge(
                    base_url=openai_base_url,
                    api_key=openai_api_key,
                    model=openai_model,
                    timeout_sec=judge_timeout,
                )
            else:
                log.warning("era_guard primary=openai_compat but missing OPENAI_API_KEY or openai_base_url/OPENAI_BASE_URL")
        else:
            log.warning("era_guard provider=%s not supported; will skip", provider)

        if fallback_provider == "openai_compat":
            if openai_api_key and openai_base_url:
                fallback_judge = OpenAICompatVisionJudge(
                    base_url=openai_base_url,
                    api_key=openai_api_key,
                    model=openai_model,
                    timeout_sec=judge_timeout,
                )
        elif fallback_provider == "qwen_compat":
            if qwen_api_key and qwen_base_url:
                fallback_judge = OpenAICompatVisionJudge(
                    base_url=qwen_base_url,
                    api_key=qwen_api_key,
                    model=qwen_model,
                    timeout_sec=judge_timeout,
                )

    vision_judge = EraGuardRouter(primary=primary_judge, fallback=fallback_judge) if (primary_judge or fallback_judge) else None
    ensure_dir(run_dir)
    scenes = script.get("scenes") or []
    if not scenes:
        raise RuntimeError("render_media_video: script.scenes is empty")

    log.info(
        "Render start: run_dir=%s preset=%s out=%sx%s@%sfps media_mode=%s total_seconds=%s scenes=%s reuse_min_score=%.1f cooldown=%s strict=%s",
        run_dir, preset_name, out_w, out_h, out_fps, media_mode, total_seconds, len(scenes), reuse_min_score, reuse_cooldown_scenes, strict_mode
    )

    allocations = [max(1, int(s.get("seconds", 1))) for s in scenes]
    ssum = sum(allocations)
    if ssum != total_seconds:
        allocations[-1] = max(1, allocations[-1] + (total_seconds - ssum))

    cooldown = max(0, int(reuse_cooldown_scenes))
    recent_keys = deque(maxlen=cooldown)  # store material keys

    clip_paths: list[str] = []
    scene_assets: list[dict[str, Any]] = []

    # hints from script/rules
    era = ""
    region = ""
    # scene prompt里被注入了 (Region:... Era:...)，这里简单从第一个scene取一下
    try:
        p0 = (scenes[0].get("image_prompt") or "") + " " + (scenes[0].get("video_prompt") or "")
        if "Region:" in p0:
            region = p0.split("Region:", 1)[1].split(")", 1)[0].strip()
        if "Era:" in p0:
            era = p0.split("Era:", 1)[1].split(")", 1)[0].strip()
    except Exception:
        pass

    required_terms = _required_terms_from_search_keywords(search_keywords_en)

    def era_guard_check(*, image_path: str, scene_id: int, kind: str) -> bool:
        if not vision_judge:
            return True
        tpl = load_text(era_guard_prompt_path)
        user_text = render_template(
            tpl,
            {
                "era": era or "unknown",
                "region": region or "unknown",
                "period": era or "unknown",
                "allowed_people_hint": allowed_people_hint,
                "hard_block_objects": hard_block_objects,
            },
        )
        if tracer:
            tracer.emit("era_guard_try", scene_id=scene_id, kind=kind, image_path=image_path, model=judge_model, base_url=judge_base_url)
        try:
            out = vision_judge.judge(
                system="你是严格的视觉内容审核器，只输出JSON。",
                user_text=user_text,
                image_path=image_path,
                schema_hint=ERA_GUARD_SCHEMA,
            )
            ok = bool(out.get("ok", True))
            if tracer:
                tracer.emit("era_guard_result", scene_id=scene_id, kind=kind, ok=ok, confidence=float(out.get("confidence") or 0.0), reasons=out.get("reasons"), detected=out.get("detected"))
            return ok
        except Exception as e:
            # 审核失败：宁缺毋滥时视为不通过；否则放行
            if tracer:
                tracer.emit("era_guard_fail", scene_id=scene_id, kind=kind, error=str(e))
            return False if strict_mode else True

    for scene, sec in zip(scenes, allocations):
        sid = int(scene["id"])
        scene_kw = _scene_query_keywords(scene)

        desired = _pick_desired(scene, media_mode)
        orientation = (scene.get("orientation") or "portrait").lower()
        importance = (scene.get("importance") or "normal").lower()

        gen_w, gen_h, gen_fps = _pick_dims_from_importance(providers_cfg, importance)
        if orientation == "landscape":
            gen_w, gen_h = max(gen_w, gen_h), min(gen_w, gen_h)
        else:
            gen_w, gen_h = min(gen_w, gen_h), max(gen_w, gen_h)

        query = _build_query(scene, search_keywords_en=search_keywords_en, category=category)

        log.info("Scene %03d start: desired=%s secs=%s orientation=%s importance=%s query=%s",
                 sid, desired, sec, orientation, importance, query[:120])

        if tracer:
            tracer.emit("search_query_built", scene_id=sid, query=query, search_keywords_en=search_keywords_en, desired=desired)

        # history 严格：外搜只用于 normal 镜头，key 镜头宁可生成
        allow_external_for_scene = True
        if category == "history" and strict_mode and importance == "key":
            allow_external_for_scene = False

        # ---------- Step A: external video search ----------
        if allow_external_for_scene and external_media and bool(external_cfg.get("enabled", False)):
            if tracer:
                tracer.emit("external_search_try", kind="video", scene_id=sid, query=query, orientation=orientation)
            vids = external_media.search_video(query=query, orientation=_orientation_str(orientation))
            if tracer:
                tracer.emit("external_search_candidates", kind="video", scene_id=sid, count=len(vids))

            chosen = None
            chosen_debug = []
            for c in vids:
                key = f"{c.provider}:{c.id}"
                if key in recent_keys:
                    continue

                score, reasons = _external_text_score(
                    cand_page_url=c.page_url or "",
                    cand_download_url=c.download_url or "",
                    query=query,
                    required_terms=required_terms,
                    banned_terms=banned_terms,
                )
                req_hits = []
                text = f"{(c.page_url or '')} {(c.download_url or '')}".lower()
                for t in required_terms:
                    if t in text:
                        req_hits.append(t)
                if len(req_hits) < min_required_terms:
                    chosen_debug.append({"key": key, "score": score, "drop": f"required_terms<{min_required_terms}", "reasons": reasons})
                    continue
                if strict_mode and score < min_match_score:
                    chosen_debug.append({"key": key, "score": score, "drop": f"score<{min_match_score}", "reasons": reasons})
                    continue

                # download a small preview (we must download anyway), then extract frame and vision judge
                downloaded = os.path.join(run_dir, f"extprev_{sid:03d}_{c.provider}_{c.id}.mp4")
                try:
                    external_media.download(cand=c, out_path=downloaded)
                    frame = os.path.join(run_dir, f"extprev_{sid:03d}_{c.provider}_{c.id}.jpg")
                    _ffmpeg_extract_frame(ffmpeg_bin, downloaded, frame, ts_sec=1.0)
                    ok = era_guard_check(image_path=frame, scene_id=sid, kind="video_frame")
                    if not ok:
                        chosen_debug.append({"key": key, "score": score, "drop": "era_guard_fail", "reasons": reasons})
                        continue
                except Exception as e:
                    chosen_debug.append({"key": key, "score": score, "drop": f"download/guard_error:{e}", "reasons": reasons})
                    continue

                chosen = c
                # reuse the preview file as downloaded
                break

            if tracer and chosen_debug:
                tracer.emit("external_strict_debug", scene_id=sid, kind="video", top=chosen_debug[:5], min_match_score=min_match_score, min_required_terms=min_required_terms)

            if chosen:
                key = f"{chosen.provider}:{chosen.id}"
                recent_keys.append(key)

                target = max(min_dur, min(int(sec), max_dur))
                downloaded = os.path.join(run_dir, f"ext_{sid:03d}_{chosen.provider}_{chosen.kind}_{chosen.id}.mp4")
                # re-download full (or overwrite preview)
                external_media.download(cand=chosen, out_path=downloaded)

                raw_clip = os.path.join(run_dir, f"rawclip_{sid:03d}.mp4")
                trim_video(
                    ffmpeg_bin=ffmpeg_bin,
                    ffprobe_bin=ffprobe_bin,
                    in_path=downloaded,
                    out_path=raw_clip,
                    target_sec=target,
                    strategy=trim_strategy,
                )
                norm_clip = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
                normalize_video_clip(ffmpeg_bin=ffmpeg_bin, in_path=raw_clip, out_path=norm_clip, width=out_w, height=out_h, fps=out_fps)

                item = library.add_item(
                    media_type="video",
                    src_file_path=raw_clip,
                    keywords=scene_kw,
                    prompt=f"[external:{chosen.provider}] {query}",
                    negative_prompt=scene.get("negative_prompt"),
                    width=gen_w,
                    height=gen_h,
                    seconds=target,
                    orientation=orientation,
                )
                if tracer:
                    tracer.emit(
                        "external_search_used",
                        kind="video",
                        scene_id=sid,
                        provider=chosen.provider,
                        id=chosen.id,
                        page_url=chosen.page_url,
                        author=chosen.author,
                        license=chosen.license_note,
                        trim_strategy=trim_strategy,
                        trimmed_seconds=target,
                        cooldown=list(recent_keys),
                        library_id=item.id,
                    )

                clip_paths.append(norm_clip)
                scene_assets.append(
                    {
                        "id": sid,
                        "type": "video",
                        "method": "external_video",
                        "material_key": key,
                        "raw_clip": raw_clip,
                        "clip": norm_clip,
                        "seconds": target,
                        "external_provider": chosen.provider,
                        "external_id": chosen.id,
                        "external_page_url": chosen.page_url,
                        "library_id": item.id,
                    }
                )
                continue

        # ---------- Step B: external image search ----------
        if allow_external_for_scene and external_media and bool(external_cfg.get("enabled", False)):
            if tracer:
                tracer.emit("external_search_try", kind="image", scene_id=sid, query=query, orientation=orientation)
            imgs = external_media.search_image(query=query, orientation=_orientation_str(orientation))
            if tracer:
                tracer.emit("external_search_candidates", kind="image", scene_id=sid, count=len(imgs))

            chosen = None
            chosen_debug = []
            for c in imgs:
                key = f"{c.provider}:{c.id}"
                if key in recent_keys:
                    continue

                score, reasons = _external_text_score(
                    cand_page_url=c.page_url or "",
                    cand_download_url=c.download_url or "",
                    query=query,
                    required_terms=required_terms,
                    banned_terms=banned_terms,
                )
                req_hits = []
                text = f"{(c.page_url or '')} {(c.download_url or '')}".lower()
                for t in required_terms:
                    if t in text:
                        req_hits.append(t)
                if len(req_hits) < min_required_terms:
                    chosen_debug.append({"key": key, "score": score, "drop": f"required_terms<{min_required_terms}", "reasons": reasons})
                    continue
                if strict_mode and score < min_match_score:
                    chosen_debug.append({"key": key, "score": score, "drop": f"score<{min_match_score}", "reasons": reasons})
                    continue

                # download and vision guard (image itself)
                img_path = os.path.join(run_dir, f"extprev_{sid:03d}_{c.provider}_{c.id}.jpg")
                try:
                    external_media.download(cand=c, out_path=img_path)
                    ok = era_guard_check(image_path=img_path, scene_id=sid, kind="image")
                    if not ok:
                        chosen_debug.append({"key": key, "score": score, "drop": "era_guard_fail", "reasons": reasons})
                        continue
                except Exception as e:
                    chosen_debug.append({"key": key, "score": score, "drop": f"download/guard_error:{e}", "reasons": reasons})
                    continue

                chosen = c
                break

            if tracer and chosen_debug:
                tracer.emit("external_strict_debug", scene_id=sid, kind="image", top=chosen_debug[:5], min_match_score=min_match_score, min_required_terms=min_required_terms)

            if chosen:
                key = f"{chosen.provider}:{chosen.id}"
                recent_keys.append(key)

                img_path = os.path.join(run_dir, f"scene_{sid:03d}.ext.png")
                external_media.download(cand=chosen, out_path=img_path)

                item = library.add_item(
                    media_type="image",
                    src_file_path=img_path,
                    keywords=scene_kw,
                    prompt=f"[external:{chosen.provider}] {query}",
                    negative_prompt=scene.get("negative_prompt"),
                    width=gen_w,
                    height=gen_h,
                    orientation=orientation,
                )
                if tracer:
                    tracer.emit(
                        "external_search_used",
                        kind="image",
                        scene_id=sid,
                        provider=chosen.provider,
                        id=chosen.id,
                        page_url=chosen.page_url,
                        author=chosen.author,
                        license=chosen.license_note,
                        cooldown=list(recent_keys),
                        library_id=item.id,
                    )

                if media_mode != "videos":
                    clip_path = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
                    image_to_motion_clip(ffmpeg_bin=ffmpeg_bin, image_path=img_path, seconds=int(sec), out_path=clip_path, width=out_w, height=out_h, fps=out_fps)
                    clip_paths.append(clip_path)
                    scene_assets.append({"id": sid, "type": "image_motion", "method": "external_image", "material_key": key, "image": img_path, "clip": clip_path, "seconds": int(sec), "library_id": item.id})
                    continue

                if not video_generator:
                    raise RuntimeError("media_mode=videos but no video_generator configured.")
                ref_b64 = _file_to_base64(img_path)
                raw_clip = os.path.join(run_dir, f"rawclip_{sid:03d}.mp4")

                if tracer:
                    tracer.emit("video_try", scene_id=sid, seconds=int(sec), method="generated_from_external_image", out_path=raw_clip)
                try:
                    video_generator.generate_clip(
                        prompt=scene.get("video_prompt") or scene.get("image_prompt") or "",
                        negative_prompt=scene.get("negative_prompt"),
                        seconds=int(sec),
                        out_path=raw_clip,
                        width=gen_w,
                        height=gen_h,
                        fps=gen_fps,
                        reference_image_base64=ref_b64,
                    )
                    if tracer:
                        tracer.emit("video_ok", scene_id=sid, seconds=int(sec), method="generated_from_external_image", out_path=raw_clip)
                except Exception as e:
                    if tracer:
                        tracer.emit("video_fail", scene_id=sid, seconds=int(sec), method="generated_from_external_image", error=str(e))
                    raise

                norm_clip = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
                normalize_video_clip(ffmpeg_bin=ffmpeg_bin, in_path=raw_clip, out_path=norm_clip, width=out_w, height=out_h, fps=out_fps)
                clip_paths.append(norm_clip)
                scene_assets.append({"id": sid, "type": "video", "method": "generated_from_external_image", "raw_clip": raw_clip, "clip": norm_clip, "seconds": int(sec)})
                continue

        # ---------- Step C: local library ----------
        if desired == "image":
            candidates = library.list_items(media_type="image", orientation=orientation)
            m = match_media(query_keywords=scene_kw, query_prompt=scene.get("image_prompt") or "", candidates=candidates, min_score=reuse_min_score)
            if m:
                img_path = m.item.file_path
                log.info("Scene %03d image reuse: id=%s score=%.1f", sid, m.item.id, m.score)
            else:
                img_path = os.path.join(run_dir, f"scene_{sid:03d}.png")
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
                log.info("Scene %03d image saved to library: id=%s", sid, item.id)

            clip_path = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
            image_to_motion_clip(ffmpeg_bin=ffmpeg_bin, image_path=img_path, seconds=int(sec), out_path=clip_path, width=out_w, height=out_h, fps=out_fps)
            clip_paths.append(clip_path)
            scene_assets.append({"id": sid, "type": "image_motion", "method": "generated_image", "image": img_path, "clip": clip_path, "seconds": int(sec)})
            continue

        v_candidates = library.list_items(media_type="video", orientation=orientation)
        mv = match_media(query_keywords=scene_kw, query_prompt=scene.get("video_prompt") or "", candidates=v_candidates, min_score=reuse_min_score)
        if mv:
            raw_clip = mv.item.file_path
            norm_clip = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
            normalize_video_clip(ffmpeg_bin=ffmpeg_bin, in_path=raw_clip, out_path=norm_clip, width=out_w, height=out_h, fps=out_fps)
            clip_paths.append(norm_clip)
            scene_assets.append({"id": sid, "type": "video", "method": "reuse_video", "raw_clip": raw_clip, "clip": norm_clip, "seconds": int(sec)})
            continue

        # ---------- Step D: generation fallback ----------
        if media_mode == "images":
            img_path = os.path.join(run_dir, f"scene_{sid:03d}.png")
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
            clip_path = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
            image_to_motion_clip(ffmpeg_bin=ffmpeg_bin, image_path=img_path, seconds=int(sec), out_path=clip_path, width=out_w, height=out_h, fps=out_fps)
            clip_paths.append(clip_path)
            scene_assets.append({"id": sid, "type": "image_motion", "method": "generated_image_fallback", "image": img_path, "clip": clip_path, "seconds": int(sec)})
            continue

        if media_mode == "mixed":
            if importance == "key":
                if not video_generator:
                    raise RuntimeError("mixed mode needs video_generator for key scenes.")
                ref_img = os.path.join(run_dir, f"ref_{sid:03d}.png")
                images.generate_image(
                    prompt=scene.get("image_prompt") or "",
                    out_path=ref_img,
                    negative_prompt=scene.get("negative_prompt"),
                    orientation=orientation,
                    importance=importance,
                    tracer=tracer,
                    step="scene_ref_image",
                    scene_id=sid,
                )
                ref_b64 = _file_to_base64(ref_img)
                raw_clip = os.path.join(run_dir, f"rawclip_{sid:03d}.mp4")

                if tracer:
                    tracer.emit("video_try", scene_id=sid, seconds=int(sec), method="generated_video_key_fallback", out_path=raw_clip)
                try:
                    video_generator.generate_clip(
                        prompt=scene.get("video_prompt") or scene.get("image_prompt") or "",
                        negative_prompt=scene.get("negative_prompt"),
                        seconds=int(sec),
                        out_path=raw_clip,
                        width=gen_w,
                        height=gen_h,
                        fps=gen_fps,
                        reference_image_base64=ref_b64,
                    )
                    if tracer:
                        tracer.emit("video_ok", scene_id=sid, seconds=int(sec), method="generated_video_key_fallback", out_path=raw_clip)
                except Exception as e:
                    if tracer:
                        tracer.emit("video_fail", scene_id=sid, seconds=int(sec), method="generated_video_key_fallback", error=str(e))
                    raise

                norm_clip = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
                normalize_video_clip(ffmpeg_bin=ffmpeg_bin, in_path=raw_clip, out_path=norm_clip, width=out_w, height=out_h, fps=out_fps)
                clip_paths.append(norm_clip)
                scene_assets.append({"id": sid, "type": "video", "method": "generated_video_key_fallback", "raw_clip": raw_clip, "clip": norm_clip, "seconds": int(sec)})
            else:
                img_path = os.path.join(run_dir, f"scene_{sid:03d}.png")
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
                clip_path = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
                image_to_motion_clip(ffmpeg_bin=ffmpeg_bin, image_path=img_path, seconds=int(sec), out_path=clip_path, width=out_w, height=out_h, fps=out_fps)
                clip_paths.append(clip_path)
                scene_assets.append({"id": sid, "type": "image_motion", "method": "generated_image_normal_fallback", "image": img_path, "clip": clip_path, "seconds": int(sec)})
            continue

        if media_mode == "videos":
            if not video_generator:
                raise RuntimeError("videos mode needs video_generator.")
            ref_img = os.path.join(run_dir, f"ref_{sid:03d}.png")
            images.generate_image(
                prompt=scene.get("image_prompt") or "",
                out_path=ref_img,
                negative_prompt=scene.get("negative_prompt"),
                orientation=orientation,
                importance=importance,
                tracer=tracer,
                step="scene_ref_image",
                scene_id=sid,
            )
            ref_b64 = _file_to_base64(ref_img)
            raw_clip = os.path.join(run_dir, f"rawclip_{sid:03d}.mp4")

            if tracer:
                tracer.emit("video_try", scene_id=sid, seconds=int(sec), method="generated_video_fallback", out_path=raw_clip)
            try:
                video_generator.generate_clip(
                    prompt=scene.get("video_prompt") or scene.get("image_prompt") or "",
                    negative_prompt=scene.get("negative_prompt"),
                    seconds=int(sec),
                    out_path=raw_clip,
                    width=gen_w,
                    height=gen_h,
                    fps=gen_fps,
                    reference_image_base64=ref_b64,
                )
                if tracer:
                    tracer.emit("video_ok", scene_id=sid, seconds=int(sec), method="generated_video_fallback", out_path=raw_clip)
            except Exception as e:
                if tracer:
                    tracer.emit("video_fail", scene_id=sid, seconds=int(sec), method="generated_video_fallback", error=str(e))
                raise

            norm_clip = os.path.join(run_dir, f"clip_{sid:03d}.mp4")
            normalize_video_clip(ffmpeg_bin=ffmpeg_bin, in_path=raw_clip, out_path=norm_clip, width=out_w, height=out_h, fps=out_fps)
            clip_paths.append(norm_clip)
            scene_assets.append({"id": sid, "type": "video", "method": "generated_video_fallback", "raw_clip": raw_clip, "clip": norm_clip, "seconds": int(sec)})
            continue

        raise RuntimeError(f"Unhandled media_mode: {media_mode}")

    narration_text = "\n".join([s.get("narration", "") for s in scenes]).strip()
    audio_path = os.path.join(run_dir, "narration.mp3")
    log.info("TTS start: voice=%s -> %s", tts_voice, audio_path)
    tts_to_mp3(text=narration_text, out_path=audio_path, voice=tts_voice)

    concat_path = os.path.join(run_dir, "concat.mp4")
    log.info("Concat start: %d clips -> %s", len(clip_paths), concat_path)
    concat_clips(ffmpeg_bin=ffmpeg_bin, clip_paths=clip_paths, out_path=concat_path)

    final_path = os.path.join(run_dir, "final.mp4")
    log.info("Mux start: video=%s audio=%s -> %s", concat_path, audio_path, final_path)
    mux_audio(ffmpeg_bin=ffmpeg_bin, video_path=concat_path, audio_path=audio_path, out_path=final_path)
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
        "reuse_cooldown_scenes": reuse_cooldown_scenes,
        "search_keywords_en": search_keywords_en,
        "external_strict_mode": strict_mode,
        "external_min_match_score": min_match_score,
        "external_min_required_terms": min_required_terms,
        "era_guard_enabled": bool(vision_judge is not None),
        "era_guard_primary": provider if vision_judge else None,
        "era_guard_fallback": fallback_provider if vision_judge else None,
        "era_guard_qwen_model": qwen_model if vision_judge else None,
        "era_guard_openai_model": openai_model if vision_judge else None,
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