from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


def load_jsonl(path: str) -> list[dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    llm_by_step = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    llm_by_provider = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    llm_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    external_used = {"videos": 0, "images": 0}
    external_candidates_seen = {"videos": 0, "images": 0}

    generated_images_by_provider = defaultdict(int)
    generated_videos = defaultdict(int)

    era_guard = {"tries": 0, "blocked": 0, "failed": 0}

    for e in events:
        ev = e.get("event")

        if ev == "llm_usage":
            step = str(e.get("step") or "unknown")
            provider = str(e.get("provider") or "unknown")
            pt = int(e.get("prompt_tokens") or 0)
            ct = int(e.get("completion_tokens") or 0)
            tt = int(e.get("total_tokens") or 0)

            llm_by_step[step]["prompt_tokens"] += pt
            llm_by_step[step]["completion_tokens"] += ct
            llm_by_step[step]["total_tokens"] += tt

            llm_by_provider[provider]["prompt_tokens"] += pt
            llm_by_provider[provider]["completion_tokens"] += ct
            llm_by_provider[provider]["total_tokens"] += tt

            llm_total["prompt_tokens"] += pt
            llm_total["completion_tokens"] += ct
            llm_total["total_tokens"] += tt

        elif ev == "external_search_candidates":
            kind = str(e.get("kind") or "")
            cnt = int(e.get("count") or 0)
            if kind == "video":
                external_candidates_seen["videos"] += cnt
            elif kind == "image":
                external_candidates_seen["images"] += cnt

        elif ev == "external_search_used":
            kind = str(e.get("kind") or "")
            if kind == "video":
                external_used["videos"] += 1
            elif kind == "image":
                external_used["images"] += 1

        elif ev == "image_ok":
            provider = str(e.get("provider") or "unknown")
            generated_images_by_provider[provider] += 1

        elif ev == "video_ok":
            method = str(e.get("method") or "unknown")
            generated_videos[method] += 1

        elif ev == "era_guard_try":
            era_guard["tries"] += 1
        elif ev == "era_guard_result":
            if not bool(e.get("ok", True)):
                era_guard["blocked"] += 1
        elif ev == "era_guard_fail":
            era_guard["failed"] += 1

    return {
        "llm": {
            "total": llm_total,
            "by_step": dict(llm_by_step),
            "by_provider": dict(llm_by_provider),
        },
        "external_media": {
            "used": external_used,
            "candidates_seen": external_candidates_seen,
        },
        "generated": {
            "images_by_provider": dict(generated_images_by_provider),
            "videos_by_method": dict(generated_videos),
        },
        "era_guard": era_guard,
    }