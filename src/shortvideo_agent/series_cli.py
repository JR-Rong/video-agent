from __future__ import annotations

import os
import json
import typer

from .config import load_settings
from .memory.store import MemoryStore

series_app = typer.Typer(add_completion=False, help="Series utilities: list & latest")


@series_app.command("list")
def list_series() -> None:
    """列出所有 series"""
    settings = load_settings()
    store = MemoryStore(sqlite_path=os.path.join(settings.data_dir, "memory.sqlite3"))
    items = store.list_series()
    if not items:
        typer.echo("No series found.")
        raise typer.Exit(code=0)
    for s in items:
        typer.echo(s)


@series_app.command("latest")
def latest(
    series: str = typer.Option(..., help="Series id"),
) -> None:
    """查看某个 series 最新一条生成记录（用于续写确认）"""
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

    # 简单摘要：取前2个镜头旁白
    scenes = rec.script.get("scenes") or []
    narr = " / ".join([(s.get("narration") or "")[:40] for s in scenes[:2]])

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