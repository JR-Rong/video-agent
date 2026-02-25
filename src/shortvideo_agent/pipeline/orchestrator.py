from __future__ import annotations

import logging
import os
import json
from typing import Any

from ..config import Settings
from ..memory.store import MemoryStore
from ..library import MediaLibrary
from ..utils.templating import load_text, render_template
from ..utils.tracing import Tracer

from ..safety import (
    load_category_allowlist,
    check_category,
    soft_match,
    hard_block_if_patterns,
    judge_block,
)

from .storyboard import OUTLINE_SCHEMA_HINT, SCRIPT_SCHEMA_HINT

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        memory: MemoryStore,
        llm: Any,
        images: Any,
        video_generator: Any | None,
        library: MediaLibrary,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.llm = llm
        self.images = images
        self.video_generator = video_generator
        self.library = library

        self.allow_categories = load_category_allowlist(settings.categories_config_path)

        self.system_prompt = load_text(os.path.join(settings.prompts_dir, "system.md"))
        self.outline_user_tpl = load_text(os.path.join(settings.prompts_dir, "outline_user.md"))
        self.script_user_tpl = load_text(os.path.join(settings.prompts_dir, "script_user.md"))

        self.safety_judge_prompt_path = os.path.join(settings.prompts_dir, "safety_judge.md")

        # era/history prompts
        self.era_constraints_tpl = load_text(os.path.join(settings.prompts_dir, "era_constraints.md"))
        self.history_factual_md = load_text(os.path.join(settings.prompts_dir, "history_factual.md"))
        self.history_classical_tpl = load_text(os.path.join(settings.prompts_dir, "history_classical_translation.md"))

    def _safety_check_with_judge(self, *, stage: str, text: str, tracer: Tracer) -> None:
        if not self.settings.strict_safety:
            return

        rr = hard_block_if_patterns(text)
        if not rr.ok:
            tracer.emit("safety_block", stage=stage, reason=rr.reason, matched_patterns=rr.matched_patterns)
            raise ValueError(f"{stage} blocked: {rr.reason}")

        sm = soft_match(text)
        matched_terms = sm.matched or []
        matched_patterns = sm.matched_patterns or []
        if matched_terms or matched_patterns:
            tracer.emit("safety_soft_hit", stage=stage, matched=matched_terms, matched_patterns=matched_patterns)
            decision = judge_block(
                llm=self.llm,
                prompt_md_path=self.safety_judge_prompt_path,
                text=text,
                matched_terms=matched_terms,
                matched_patterns=matched_patterns,
                tracer=tracer,
                step=f"safety_judge_{stage}",
            )
            tracer.emit("safety_judge_result", stage=stage, **decision)
            if decision.get("should_block"):
                raise ValueError(f"{stage} blocked: {decision.get('reason')}")

    def _build_era_constraints_text(self, *, category: str, rules: dict[str, Any]) -> str:
        era = str(rules.get("era") or "").strip() or "未指定"
        genre = str(rules.get("genre") or ("realism" if category == "history" else "realism")).strip()
        extra = str(rules.get("extra") or "").strip()
        return render_template(self.era_constraints_tpl, {"era": era, "genre": genre, "extra": extra})

    def _build_history_constraints_text(self, *, category: str, rules: dict[str, Any]) -> str:
        if category != "history":
            return ""
        mode = str(rules.get("history_mode") or "factual").strip()

        if mode == "classical_translation":
            classical_text = str(rules.get("classical_text") or "").strip()
            if not classical_text:
                # 未提供古文则退回史实叙述
                return self.history_factual_md + "\n（提示：未提供 classical_text，已退回史实叙述模式）"
            return render_template(self.history_classical_tpl, {"classical_text": classical_text})

        # 默认：史实叙述（可合理补全）
        return self.history_factual_md

    def generate(
        self,
        *,
        category: str,
        series: str,
        prompt: str,
        total_seconds: int,
        scenes: int,
        media_mode: str,
        dry_run: bool,
        reuse_min_score: float,
        series_overview: str | None = None,
        series_rules: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        r1 = check_category(category, self.allow_categories)
        if not r1.ok:
            raise ValueError(r1.reason)

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

        run_dir = os.path.join(self.settings.output_dir, series, f"ep_{episode}")
        tracer = Tracer(trace_path=os.path.join(run_dir, "trace.jsonl"))
        tracer.emit("run_start", category=category, series=series, episode=episode, media_mode=media_mode, scenes=scenes, total_seconds=total_seconds)

        rules = series_rules or {}
        era_constraints = self._build_era_constraints_text(category=category, rules=rules)
        history_constraints = self._build_history_constraints_text(category=category, rules=rules)

        tracer.emit("constraints_loaded", era=str(rules.get("era") or ""), history_mode=str(rules.get("history_mode") or "factual"))

        # safety on user prompt
        self._safety_check_with_judge(stage="user_prompt", text=prompt, tracer=tracer)

        # Outline
        outline_user = render_template(
            self.outline_user_tpl,
            {
                "category": category,
                "prompt": prompt,
                "series": series,
                "episode": episode,
                "prev_context": prev_context,
                "era_constraints": era_constraints,
                "history_constraints": history_constraints,
            },
        )
        if series_overview:
            outline_user = "【系列总概述】\n" + series_overview.strip() + "\n\n" + outline_user

        tracer.emit("llm_outline_start")
        outline = self.llm.json_generate(system=self.system_prompt, user=outline_user, schema_hint=OUTLINE_SCHEMA_HINT, tracer=tracer, step="outline")
        tracer.emit("llm_outline_done")
        self._safety_check_with_judge(stage="outline", text=str(outline), tracer=tracer)

        # Script
        script_user = render_template(
            self.script_user_tpl,
            {
                "total_seconds": total_seconds,
                "scenes": scenes,
                "series": series,
                "episode": episode,
                "outline": outline,
                "era_constraints": era_constraints,
                "history_constraints": history_constraints,
            },
        )
        tracer.emit("llm_script_start")
        script = self.llm.json_generate(system=self.system_prompt, user=script_user, schema_hint=SCRIPT_SCHEMA_HINT, tracer=tracer, step="script")
        tracer.emit("llm_script_done")
        self._safety_check_with_judge(stage="script", text=str(script), tracer=tracer)

        # normalize
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
                    "seconds": 15,
                    "importance": "normal",
                    "orientation": "portrait",
                    "media_type": "image",
                    "narration": "（补充镜头）",
                    "on_screen_text": "",
                    "image_prompt": "竖屏，符合时代背景的过渡画面，柔和光影，电影感",
                    "video_prompt": "竖屏，符合时代背景的过渡镜头，轻微镜头运动，电影感",
                    "negative_prompt": "现代, 手机, 汽车, 自行车, 西装, 互联网, 高楼, 霓虹灯",
                }
            )

        era_tag = str(rules.get("era") or "").strip()

        for i, sc in enumerate(script["scenes"], start=1):
            sc["id"] = i
            sc["media_type"] = (sc.get("media_type") or "image").lower()
            sc["importance"] = (sc.get("importance") or "normal").lower()
            sc["orientation"] = (sc.get("orientation") or "portrait").lower()
            sc["image_prompt"] = sc.get("image_prompt") or sc.get("video_prompt") or ""
            sc["video_prompt"] = sc.get("video_prompt") or sc.get("image_prompt") or ""
            sc["negative_prompt"] = sc.get("negative_prompt") or "现代, 手机, 汽车, 自行车, 西装, 互联网, 高楼, 霓虹灯"
            try:
                sc["seconds"] = int(sc.get("seconds", 15))
            except Exception:
                sc["seconds"] = 15
            if sc["seconds"] < 1:
                sc["seconds"] = 1

            if era_tag:
                sc["image_prompt"] += f"。时代：{era_tag}。服饰与器物必须符合该时代。"
                sc["video_prompt"] += f"。时代：{era_tag}。服饰与器物必须符合该时代。"

        # long-form allocation (10~40 soft, 5~60 hard)
        soft_lo, soft_hi = 10, 40
        hard_lo, hard_hi = 5, 60
        secs_list = []
        for sc in script["scenes"]:
            s = int(sc.get("seconds", 15))
            s = max(soft_lo, min(soft_hi, s))
            secs_list.append(s)

        delta = total_seconds - sum(secs_list)
        idx = 0
        while delta != 0 and idx < 200000:
            j = idx % len(secs_list)
            if delta > 0 and secs_list[j] < hard_hi:
                secs_list[j] += 1
                delta -= 1
            elif delta < 0 and secs_list[j] > hard_lo:
                secs_list[j] -= 1
                delta += 1
            idx += 1
        if delta != 0:
            secs_list[-1] = max(hard_lo, min(hard_hi, secs_list[-1] + delta))

        for sc, s in zip(script["scenes"], secs_list):
            sc["seconds"] = int(s)

        assets: dict[str, Any] = {}
        final_video_path: str | None = None
        export_dir: str | None = None

        if not dry_run:
            from .render import render_media_video, export_final
            tracer.emit("render_start", run_dir=run_dir)
            assets, final_video_path = render_media_video(
                run_dir=run_dir,
                ffmpeg_bin=self.settings.ffmpeg_bin,
                render_presets_path=self.settings.render_presets_config_path,
                preset_name=self.settings.render_preset,
                providers_config_path=self.settings.providers_config_path,
                script=script,
                images=self.images,
                video_generator=self.video_generator,
                library=self.library,
                total_seconds=total_seconds,
                media_mode=media_mode,
                tts_voice=self.settings.tts_voice,
                reuse_min_score=reuse_min_score,
                tracer=tracer,
            )
            tracer.emit("render_done", final_video_path=final_video_path)

            export_dir = export_final(
                output_routing_path=self.settings.output_routing_config_path,
                export_root_default=self.settings.export_dir,
                category=category,
                series=series,
                episode=episode,
                final_video_path=final_video_path,
                script=script,
                outline=outline,
                tracer=tracer,
            )
            tracer.emit("export_done", export_dir=export_dir)

        rec_id = self.memory.add_record(
            series=series,
            category=category,
            user_prompt=prompt,
            outline=outline,
            script=script,
            assets=assets,
            final_video_path=final_video_path,
        )

        tracer.emit("run_end", record_id=rec_id)

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
            "trace_path": os.path.join(run_dir, "trace.jsonl"),
        }