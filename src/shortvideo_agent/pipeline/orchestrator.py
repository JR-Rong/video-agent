from __future__ import annotations

import logging
import os
from typing import Any

from ..config import Settings
from ..memory.store import MemoryStore
from ..providers.llm_openai import OpenAILLM
from ..providers.images_openai import OpenAIImages
from ..providers.video_clips_openai import OpenAIVideoClips
from ..safety.policy import check_text_policy, load_category_allowlist, check_category
from ..utils.templating import load_text, render_template
from .storyboard import OUTLINE_SCHEMA_HINT, SCRIPT_SCHEMA_HINT

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        memory: MemoryStore,
        llm: OpenAILLM,
        images: OpenAIImages,
        video_clips: OpenAIVideoClips | None,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.llm = llm
        self.images = images
        self.video_clips = video_clips

        self.allow_categories = load_category_allowlist(settings.categories_config_path)

        self.system_prompt = load_text(os.path.join(settings.prompts_dir, "system.md"))
        self.outline_user_tpl = load_text(os.path.join(settings.prompts_dir, "outline_user.md"))
        self.script_user_tpl = load_text(os.path.join(settings.prompts_dir, "script_user.md"))

    def generate(
        self,
        *,
        category: str,
        series: str,
        prompt: str,
        total_seconds: int,
        scenes: int,
        mode: str,  # reserved
        media_mode: str,  # images|videos|mixed
        dry_run: bool,
    ) -> dict[str, Any]:
        r1 = check_category(category, self.allow_categories)
        if not r1.ok:
            raise ValueError(r1.reason)

        r2 = check_text_policy(prompt)
        if not r2.ok:
            raise ValueError(f"Prompt blocked: {r2.reason}")

        prev = self.memory.get_latest_by_series(series)
        prev_context = ""
        episode = 1
        if prev:
            episode = int(prev.script.get("episode", 1)) + 1
            prev_context = (
                "以下是上一次已生成内容（用于续写，必须保持人物设定一致，但剧情必须原创推进）：\n"
                f"标题：{prev.outline.get('title','')}\n"
                f"剧情要点：{prev.outline.get('plot_points',[])}\n"
                f"上集脚本摘要：{[s.get('narration','') for s in prev.script.get('scenes',[])]}\n"
            )

        outline_user = render_template(
            self.outline_user_tpl,
            {"category": category, "prompt": prompt, "series": series, "episode": episode, "prev_context": prev_context},
        )
        outline = self.llm.json_generate(system=self.system_prompt, user=outline_user, schema_hint=OUTLINE_SCHEMA_HINT)

        if self.settings.strict_safety:
            rr = check_text_policy(str(outline))
            if not rr.ok:
                raise ValueError(f"Generated outline blocked: {rr.reason}")

        script_user = render_template(
            self.script_user_tpl,
            {"total_seconds": total_seconds, "scenes": scenes, "series": series, "episode": episode, "outline": outline},
        )
        script = self.llm.json_generate(system=self.system_prompt, user=script_user, schema_hint=SCRIPT_SCHEMA_HINT)

        if self.settings.strict_safety:
            rr = check_text_policy(str(script))
            if not rr.ok:
                raise ValueError(f"Generated script blocked: {rr.reason}")

        # Normalize scenes count + ids
        script["series"] = series
        script["episode"] = episode
        script["total_seconds"] = total_seconds
        if "scenes" not in script or not isinstance(script["scenes"], list) or len(script["scenes"]) == 0:
            raise ValueError("Invalid script: missing scenes.")

        script["scenes"] = script["scenes"][:scenes]
        while len(script["scenes"]) < scenes:
            script["scenes"].append(
                {
                    "id": len(script["scenes"]) + 1,
                    "seconds": max(1, total_seconds // scenes),
                    "media_type": "image",
                    "narration": "（补充镜头）",
                    "on_screen_text": "",
                    "image_prompt": "竖屏，抽象过渡画面，柔和光影，电影感",
                    "video_prompt": "竖屏，抽象过渡镜头，轻微镜头运动，柔和光影，电影感",
                    "negative_prompt": "新闻，政治，真实人物，logo，水印",
                }
            )
        for i, sc in enumerate(script["scenes"], start=1):
            sc["id"] = i
            sc["media_type"] = (sc.get("media_type") or "image").lower()
            sc["video_prompt"] = sc.get("video_prompt") or sc.get("image_prompt") or ""
            sc["image_prompt"] = sc.get("image_prompt") or sc.get("video_prompt") or ""

        # distribute seconds
        base = max(1, total_seconds // scenes)
        secs = [base] * scenes
        remainder = total_seconds - sum(secs)
        for i in range(abs(remainder)):
            idx = i % scenes
            secs[idx] += 1 if remainder > 0 else -1
            if secs[idx] < 1:
                secs[idx] = 1
        for sc, s in zip(script["scenes"], secs):
            sc["seconds"] = int(s)

        assets: dict[str, Any] = {}
        final_video_path: str | None = None
        export_dir: str | None = None

        if not dry_run:
            safe_series = series
            run_dir = os.path.join(self.settings.output_dir, safe_series, f"ep_{episode}")

            from .render import render_media_video, export_final

            assets, final_video_path = render_media_video(
                run_dir=run_dir,
                ffmpeg_bin=self.settings.ffmpeg_bin,
                render_presets_path=self.settings.render_presets_config_path,
                preset_name=self.settings.render_preset,
                script=script,
                images=self.images,
                video_clips=self.video_clips,
                total_seconds=total_seconds,
                media_mode=media_mode,
                tts_voice=self.settings.tts_voice,
            )

            export_dir = export_final(
                output_routing_path=self.settings.output_routing_config_path,
                export_root_default=self.settings.export_dir,
                category=category,
                series=series,
                episode=episode,
                final_video_path=final_video_path,
                script=script,
                outline=outline,
            )

        rec_id = self.memory.add_record(
            series=series,
            category=category,
            user_prompt=prompt,
            outline=outline,
            script=script,
            assets=assets,
            final_video_path=final_video_path,
        )

        return {
            "record_id": rec_id,
            "series": series,
            "episode": episode,
            "category": category,
            "outline": outline,
            "script": script,
            "assets": assets,
            "final_video_path": final_video_path,
            "export_dir": export_dir,
        }