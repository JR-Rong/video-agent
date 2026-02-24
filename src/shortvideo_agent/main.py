from __future__ import annotations

import json
import logging
import os
from typing import Optional

import typer

from .config import load_settings
from .logging_conf import setup_logging
from .memory.store import MemoryStore
from .providers.images_openai import OpenAIImages
from .providers.llm_openai import OpenAILLM
from .providers.video_clips_openai import OpenAIVideoClips
from .utils.files import ensure_dir
from .utils.text import clamp_int
from .pipeline.orchestrator import Orchestrator

app = typer.Typer(add_completion=False)
log = logging.getLogger(__name__)


@app.command()
def run(
    category: str = typer.Option(..., help="Category name from configs/categories.yaml allowlist"),
    series: str = typer.Option(..., help="Series id for memory/continuation"),
    prompt: str = typer.Option(..., help="User topic prompt (must be non-news/non-politics)"),
    total_seconds: int = typer.Option(30, help="Total video duration seconds"),
    scenes: int = typer.Option(6, help="Number of scenes/shots"),
    media_mode: str = typer.Option("images", help="images|videos|mixed (default images)"),
    dry_run: bool = typer.Option(False, help="Only generate outline/script, no media"),
    out_json: Optional[str] = typer.Option(None, help="Write result json to path"),
) -> None:
    setup_logging()

    settings = load_settings()
    ensure_dir(settings.data_dir)
    ensure_dir(settings.output_dir)
    ensure_dir(settings.export_dir)

    total_seconds = clamp_int(total_seconds, 5, 600)
    scenes = clamp_int(scenes, 1, 30)

    memory = MemoryStore(sqlite_path=os.path.join(settings.data_dir, "memory.sqlite3"))
    llm = OpenAILLM(api_key=settings.openai_api_key, base_url=settings.openai_base_url, model=settings.openai_text_model)
    images = OpenAIImages(api_key=settings.openai_api_key, base_url=settings.openai_base_url, model=settings.openai_image_model)

    video_clips = OpenAIVideoClips(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_video_model,
    )

    orch = Orchestrator(settings=settings, memory=memory, llm=llm, images=images, video_clips=video_clips)

    result = orch.generate(
        category=category,
        series=series,
        prompt=prompt,
        total_seconds=total_seconds,
        scenes=scenes,
        mode="default",
        media_mode=media_mode,
        dry_run=dry_run,
    )

    text = json.dumps(result, ensure_ascii=False, indent=2)
    typer.echo(text)

    if out_json:
        os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            f.write(text)
        log.info("Saved result json to %s", out_json)


if __name__ == "__main__":
    app()