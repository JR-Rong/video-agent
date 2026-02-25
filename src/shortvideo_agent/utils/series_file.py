from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import yaml


@dataclass
class SeriesEpisodeSpec:
    prompt: str
    total_seconds: int | None = None
    scenes: int | None = None
    media_mode: str | None = None
    reuse_min_score: float | None = None
    dry_run: bool | None = None


@dataclass
class SeriesSpec:
    series: str
    category: str
    overview: str
    rules: dict[str, Any]
    episodes: list[SeriesEpisodeSpec]


def load_series_file(path: str) -> SeriesSpec:
    with open(path, "r", encoding="utf-8") as f:
        obj = yaml.safe_load(f) or {}

    if not obj.get("series") or not obj.get("category") or not obj.get("overview"):
        raise ValueError("series-file must include: series, category, overview")

    eps_raw = obj.get("episodes") or []
    if not isinstance(eps_raw, list) or not eps_raw:
        raise ValueError("series-file must include non-empty episodes list")

    episodes: list[SeriesEpisodeSpec] = []
    for e in eps_raw:
        if not e.get("prompt"):
            raise ValueError("each episode must include prompt")
        episodes.append(
            SeriesEpisodeSpec(
                prompt=str(e["prompt"]),
                total_seconds=e.get("total_seconds"),
                scenes=e.get("scenes"),
                media_mode=e.get("media_mode"),
                reuse_min_score=e.get("reuse_min_score"),
                dry_run=e.get("dry_run"),
            )
        )

    return SeriesSpec(
        series=str(obj["series"]),
        category=str(obj["category"]),
        overview=str(obj["overview"]),
        rules=obj.get("rules") or {},
        episodes=episodes,
    )