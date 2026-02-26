from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional, Any

import typer
import yaml

from .config import load_settings
from .logging_conf import setup_logging
from .memory.store import MemoryStore
from .library import MediaLibrary
from .utils.files import ensure_dir
from .utils.text import clamp_int
from .utils.args_file import find_args_file, parse_args_file, coerce_types
from .utils.series_file import load_series_file
from .utils.trace_stats import load_jsonl, summarize_trace

from .providers.llm_router import build_llm
from .providers.images_router import build_images
from .providers.video_router import build_video_generator
from .providers.external_media.router import build_external_media
from .pipeline.orchestrator import Orchestrator

app = typer.Typer(add_completion=False)
log = logging.getLogger(__name__)

series_app = typer.Typer(add_completion=False, help="Series utilities: list & latest")
app.add_typer(series_app, name="series")


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _maybe_show_kling_costs(video_gen: Any | None, providers_config_path: str) -> dict[str, Any] | None:
    if not video_gen or not hasattr(video_gen, "get_costs"):
        return None

    cfg = _load_yaml(providers_config_path)
    kc = cfg.get("kling_costs") or {}
    lookback_days = int(kc.get("lookback_days") or 30)
    resource_pack_name = (kc.get("resource_pack_name") or "").strip() or None

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - lookback_days * 24 * 3600 * 1000

    try:
        data = video_gen.get_costs(start_time_ms=start_ms, end_time_ms=end_ms, resource_pack_name=resource_pack_name)
        infos = (data.get("resource_pack_subscribe_infos") or [])
        log.info("Kling costs/resource packs (lookback_days=%s): %s", lookback_days, len(infos))
        for it in infos:
            log.info(
                " - %s | remaining=%s / total=%s | status=%s | invalid_time=%s",
                it.get("resource_pack_name"),
                it.get("remaining_quantity"),
                it.get("total_quantity"),
                it.get("status"),
                it.get("invalid_time"),
            )
        return data
    except Exception as e:
        log.warning("Failed to query Kling costs: %s", e)
        return None


def _auto_scenes(total_seconds: int) -> int:
    target = 20
    s = round(total_seconds / target)
    return clamp_int(s, 6, 20)


def _apply_args_file_defaults(project_dir: str, cli: dict[str, Any]) -> dict[str, Any]:
    args_path = find_args_file(project_dir)
    if not args_path:
        cli["args_path"] = None
        return cli

    raw = parse_args_file(args_path)
    cfg: dict[str, Any] = {k.lstrip("-"): coerce_types(k, v) for k, v in raw.items()}
    log.info("Loaded .args: %s", args_path)

    def pick(key: str, default=None):
        return cli[key] if cli.get(key) is not None else cfg.get(key, default)

    merged = dict(cli)
    merged["category"] = pick("category")
    merged["series"] = pick("series")
    merged["prompt"] = pick("prompt")
    merged["series_file"] = pick("series-file")
    merged["total_seconds"] = pick("total-seconds")
    merged["scenes"] = pick("scenes")
    merged["media_mode"] = pick("media-mode", "images")
    merged["reuse_min_score"] = pick("reuse-min-score", 78.0)
    merged["dry_run"] = pick("dry-run", False)
    merged["out_json"] = pick("out-json", None)
    merged["args_path"] = args_path
    return merged


@app.command()
def stats(
    trace: str = typer.Option(..., help="trace.jsonl path, e.g. data/outputs/xxx/ep_1/trace.jsonl"),
) -> None:
    """
    从 trace.jsonl 汇总：
    - LLM token usage（优先 provider usage，缺失则估算）
    - 外搜使用的图片/视频数、候选数
    - 生成的图片/视频数
    - era_guard 尝试/拦截数
    """
    setup_logging()
    events = load_jsonl(trace)
    out = summarize_trace(events)
    typer.echo(json.dumps(out, ensure_ascii=False, indent=2))


@app.command()
def run(
    category: Optional[str] = typer.Option(None, help="内容类别（不传则尝试从 .args 读取）"),
    series: Optional[str] = typer.Option(None, help="系列ID（不传则尝试从 .args 读取）"),
    prompt: Optional[str] = typer.Option(None, help="主题提示词（与 series-file 互斥）"),
    series_file: Optional[str] = typer.Option(None, help="系列批量生成文件路径（YAML，与 prompt 互斥）"),
    total_seconds: Optional[int] = typer.Option(None, help="总时长秒（<=300；不传则尝试从 .args 读取）"),
    scenes: Optional[int] = typer.Option(None, help="镜头数（不传则自动估算或从 .args 读取）"),
    media_mode: Optional[str] = typer.Option(None, help="images|videos|mixed（不传则尝试从 .args 读取）"),
    reuse_min_score: Optional[float] = typer.Option(None, help="素材库复用阈值 0-100（不传则尝试从 .args 读取）"),
    dry_run: Optional[bool] = typer.Option(None, help="只生成脚本不生成媒体（true/false；不传则尝试从 .args 读取）"),
    out_json: Optional[str] = typer.Option(None, help="将结果写入JSON文件（不传则尝试从 .args 读取）"),
) -> None:
    setup_logging()
    settings = load_settings()

    cli = {
        "category": category,
        "series": series,
        "prompt": prompt,
        "series-file": series_file,
        "total-seconds": total_seconds,
        "scenes": scenes,
        "media-mode": media_mode,
        "reuse-min-score": reuse_min_score,
        "dry-run": dry_run,
        "out-json": out_json,
    }
    merged = _apply_args_file_defaults(os.getcwd(), cli)

    category_v = merged.get("category")
    series_v = merged.get("series")
    prompt_v = merged.get("prompt")
    series_file_v = merged.get("series_file")

    if prompt_v and series_file_v:
        raise RuntimeError("参数冲突：prompt 与 series-file 互斥，只能传一个。")

    ensure_dir(settings.data_dir)
    ensure_dir(settings.output_dir)
    ensure_dir(settings.export_dir)
    ensure_dir(settings.media_library_dir)

    memory = MemoryStore(sqlite_path=os.path.join(settings.data_dir, "memory.sqlite3"))
    library = MediaLibrary(root_dir=settings.media_library_dir)

    llm = build_llm(settings=settings)
    images = build_images(settings=settings)
    video_generator = build_video_generator(settings=settings)
    external_media = build_external_media(settings=settings)

    kling_costs = _maybe_show_kling_costs(video_generator, settings.providers_config_path)

    orch = Orchestrator(
        settings=settings,
        memory=memory,
        llm=llm,
        images=images,
        video_generator=video_generator,
        library=library,
        external_media=external_media,
    )

    # ---------- Batch mode ----------
    if series_file_v:
        spec = load_series_file(series_file_v)

        category_v = spec.category
        series_v = spec.series
        series_overview = spec.overview
        series_rules = spec.rules

        results = []
        for idx, ep in enumerate(spec.episodes, start=1):
            ts = ep.total_seconds if ep.total_seconds is not None else (merged.get("total_seconds") or 30)
            ts = clamp_int(int(ts), 5, 300)

            sc = ep.scenes if ep.scenes is not None else merged.get("scenes")
            if sc is None:
                sc = _auto_scenes(ts)
                log.info("Episode %s scenes auto=%s for total_seconds=%s", idx, sc, ts)
            sc = clamp_int(int(sc), 3, 40)

            mm = ep.media_mode if ep.media_mode else (merged.get("media_mode") or "images")
            rm = ep.reuse_min_score if ep.reuse_min_score is not None else float(merged.get("reuse_min_score") or 78.0)
            rm = max(0.0, min(100.0, rm))
            dr = ep.dry_run if ep.dry_run is not None else bool(merged.get("dry_run") or False)

            log.info("Batch episode %s start: series=%s category=%s seconds=%s scenes=%s media_mode=%s", idx, series_v, category_v, ts, sc, mm)

            r = orch.generate(
                category=category_v,
                series=series_v,
                prompt=ep.prompt,
                total_seconds=ts,
                scenes=sc,
                media_mode=mm,
                dry_run=dr,
                reuse_min_score=rm,
                series_overview=series_overview,
                series_rules=series_rules,
            )
            results.append(r)

        out = {"mode": "batch", "series_file": series_file_v, "results": results}
        if kling_costs is not None:
            out["kling_costs"] = kling_costs

        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))

        if merged.get("out_json"):
            out_json_v = merged["out_json"]
            os.makedirs(os.path.dirname(out_json_v) or ".", exist_ok=True)
            with open(out_json_v, "w", encoding="utf-8") as f:
                f.write(json.dumps(out, ensure_ascii=False, indent=2))
            log.info("Saved batch result json to %s", out_json_v)
        return

    # ---------- Single mode ----------
    if not category_v or not series_v or not prompt_v:
        raise RuntimeError(
            "缺少必填参数：category/series/prompt。\n"
            "你可以：\n"
            "1) 传入命令行参数；或\n"
            "2) 在项目根目录创建 .args 文件（参考 .args.example）；或\n"
            "3) 使用 --series-file 批量生成"
        )

    ts = merged.get("total_seconds") if merged.get("total_seconds") is not None else 30
    ts = clamp_int(int(ts), 5, 300)

    sc = merged.get("scenes")
    if sc is None:
        sc = _auto_scenes(ts)
        log.info("Scenes not provided. Auto scenes=%s for total_seconds=%s", sc, ts)
    sc = clamp_int(int(sc), 3, 40)

    mm = (merged.get("media_mode") or "images").strip()
    rm = float(merged.get("reuse_min_score") or 78.0)
    rm = max(0.0, min(100.0, rm))
    dr = bool(merged.get("dry_run") or False)

    result = orch.generate(
        category=category_v,
        series=series_v,
        prompt=prompt_v,
        total_seconds=ts,
        scenes=sc,
        media_mode=mm,
        dry_run=dr,
        reuse_min_score=rm,
        series_overview=None,
        series_rules=None,
    )

    if kling_costs is not None:
        result["kling_costs"] = kling_costs
    if merged.get("args_path"):
        result["args_file"] = merged["args_path"]

    text = json.dumps(result, ensure_ascii=False, indent=2)
    typer.echo(text)

    if merged.get("out_json"):
        out_json_v = merged["out_json"]
        os.makedirs(os.path.dirname(out_json_v) or ".", exist_ok=True)
        with open(out_json_v, "w", encoding="utf-8") as f:
            f.write(text)
        log.info("Saved result json to %s", out_json_v)


@series_app.command("list")
def series_list() -> None:
    setup_logging()
    settings = load_settings()
    store = MemoryStore(sqlite_path=os.path.join(settings.data_dir, "memory.sqlite3"))
    items = store.list_series()
    if not items:
        typer.echo("No series found.")
        raise typer.Exit(code=0)
    for s in items:
        typer.echo(s)


@series_app.command("latest")
def series_latest(series: str = typer.Option(..., help="Series id")) -> None:
    setup_logging()
    settings = load_settings()
    store = MemoryStore(sqlite_path=os.path.join(settings.data_dir, "memory.sqlite3"))
    rec = store.get_latest_by_series(series)
    if not rec:
        typer.echo(f"No record found for series: {series}")
        raise typer.Exit(code=1)

    ep = rec.script.get("episode")
    title = rec.script.get("title") or rec.outline.get("title")
    created_at = rec.created_at
    final_path = rec.final_video_path

    scenes = rec.script.get("scenes") or []
    narr = " / ".join([(s.get("narration") or "")[:60] for s in scenes[:2]])

    out = {
        "id": rec.id,
        "series": rec.series,
        "category": rec.category,
        "episode": ep,
        "title": title,
        "created_at": created_at,
        "final_video_path": final_path,
        "user_prompt": rec.user_prompt,
        "narration_preview": narr,
    }
    typer.echo(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()