from __future__ import annotations

import json
import logging
import os
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

SEARCH_KW_SCHEMA = """{
  "keywords_en": "string"
}"""

ERA_PROFILE_SCHEMA = """{
  "setting": {
    "region": "string",
    "period": "string",
    "era": "string",
    "culture_style": "string",
    "genre": "realism|fantasy|sci_fi"
  },
  "search_keywords_en": {
    "required_terms": ["string"],
    "optional_terms": ["string"],
    "avoid_terms": ["string"]
  },
  "visual_rules": {
    "allowed_people_hint": "string",
    "banned_objects": ["string"],
    "banned_styles": ["string"],
    "notes": "string"
  },
  "confidence": 0.0
}"""


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
        external_media: Any | None,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.llm = llm
        self.images = images
        self.video_generator = video_generator
        self.library = library
        self.external_media = external_media

        self.allow_categories = load_category_allowlist(settings.categories_config_path)

        # prompts
        self.system_prompt = load_text(os.path.join(settings.prompts_dir, "system.md"))
        self.outline_user_tpl = load_text(os.path.join(settings.prompts_dir, "outline_user.md"))
        self.script_user_tpl = load_text(os.path.join(settings.prompts_dir, "script_user.md"))

        self.safety_judge_prompt_path = os.path.join(settings.prompts_dir, "safety_judge.md")

        self.era_constraints_tpl = load_text(os.path.join(settings.prompts_dir, "era_constraints.md"))
        self.history_factual_md = load_text(os.path.join(settings.prompts_dir, "history_factual.md"))
        self.history_classical_tpl = load_text(os.path.join(settings.prompts_dir, "history_classical_translation.md"))

        # new: era profile extraction
        self.era_profile_tpl = load_text(os.path.join(settings.prompts_dir, "era_profile.md"))

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
        era = str(rules.get("era") or rules.get("period") or "").strip() or "未指定"
        genre = str(rules.get("genre") or ("realism" if category == "history" else "realism")).strip()
        extra = str(rules.get("extra") or "").strip()
        region = str(rules.get("region") or "").strip()
        period = str(rules.get("period") or "").strip()
        if region or period:
            extra2 = f"地域：{region or '未指定'}；时期：{period or '未指定'}"
            extra = (extra2 + ("\n" + extra if extra else "")).strip()
        return render_template(self.era_constraints_tpl, {"era": era, "genre": genre, "extra": extra})

    def _build_history_constraints_text(self, *, category: str, rules: dict[str, Any]) -> str:
        if category != "history":
            return ""
        mode = str(rules.get("history_mode") or "factual").strip()
        if mode == "classical_translation":
            classical_text = str(rules.get("classical_text") or "").strip()
            if not classical_text:
                return self.history_factual_md + "\n（提示：未提供 classical_text，已退回史实叙述模式）"
            return render_template(self.history_classical_tpl, {"classical_text": classical_text})
        return self.history_factual_md

    def _translate_search_constraints(
        self,
        *,
        category: str,
        rules: dict[str, Any],
        tracer: Tracer,
    ) -> str:
        region = str(rules.get("region") or "").strip()
        period = str(rules.get("period") or "").strip()
        era = str(rules.get("era") or "").strip()
        genre = str(rules.get("genre") or "").strip() or ("realism" if category == "history" else "realism")

        if not (region or period or era):
            return ""

        user = (
            "请把以下“地域+时期/时代”转换为适合外网素材平台检索的英文关键词（短语即可）。\n"
            "要求：\n"
            "1) 输出只包含一个JSON对象 {\"keywords_en\":\"...\"}\n"
            "2) keywords_en 只写英文关键词短语，不要中文，不要解释\n"
            "3) 尽量包含：国家/地区英文名 + 时期英文名 + 历史/风格词（ancient/medieval/traditional/architecture等）\n"
            "4) 若信息不足，也输出通用词\n\n"
            f"category={category}\n"
            f"genre={genre}\n"
            f"region(中文)={region}\n"
            f"period(中文)={period}\n"
            f"era(中文)={era}\n"
        )

        tracer.emit("search_constraints_translate_start", region=region, period=period, era=era)
        out = self.llm.json_generate(
            system="只输出JSON。",
            user=user,
            schema_hint=SEARCH_KW_SCHEMA,
            tracer=tracer,
            step="search_constraints_translate",
        )
        kw = str(out.get("keywords_en") or "").strip()
        tracer.emit("search_constraints_translate_done", keywords_en=kw)
        return kw

    def _infer_era_profile(self, *, prompt: str, outline: dict[str, Any], tracer: Tracer) -> dict[str, Any]:
        user = render_template(
            self.era_profile_tpl,
            {"prompt": prompt, "outline_json": json.dumps(outline, ensure_ascii=False)},
        )
        tracer.emit("era_profile_start")
        out = self.llm.json_generate(
            system="你是信息抽取器，只输出JSON。",
            user=user,
            schema_hint=ERA_PROFILE_SCHEMA,
            tracer=tracer,
            step="era_profile",
        )
        tracer.emit("era_profile_done", confidence=float(out.get("confidence") or 0.0))
        return out

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
        tracer.emit(
            "run_start",
            category=category,
            series=series,
            episode=episode,
            media_mode=media_mode,
            scenes=scenes,
            total_seconds=total_seconds,
        )

        rules = series_rules or {}
        era_constraints = self._build_era_constraints_text(category=category, rules=rules)
        history_constraints = self._build_history_constraints_text(category=category, rules=rules)
        search_keywords_en = self._translate_search_constraints(category=category, rules=rules, tracer=tracer)

        tracer.emit(
            "constraints_loaded",
            region=str(rules.get("region") or ""),
            period=str(rules.get("period") or ""),
            era=str(rules.get("era") or ""),
            search_keywords_en=search_keywords_en,
            reuse_cooldown_scenes=int(rules.get("reuse_cooldown_scenes") or 2),
        )

        self._safety_check_with_judge(stage="user_prompt", text=prompt, tracer=tracer)

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
        outline = self.llm.json_generate(
            system=self.system_prompt,
            user=outline_user,
            schema_hint=OUTLINE_SCHEMA_HINT,
            tracer=tracer,
            step="outline",
        )
        tracer.emit("llm_outline_done")
        self._safety_check_with_judge(stage="outline", text=str(outline), tracer=tracer)

        # new: infer era profile from outline+prompt (generic, not China-specific)
        era_profile = self._infer_era_profile(prompt=prompt, outline=outline, tracer=tracer)

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
        script = self.llm.json_generate(
            system=self.system_prompt,
            user=script_user,
            schema_hint=SCRIPT_SCHEMA_HINT,
            tracer=tracer,
            step="script",
        )
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
                    "image_prompt": "竖屏，符合故事设定的过渡画面，电影感",
                    "video_prompt": "竖屏，符合故事设定的过渡镜头，电影感",
                    "negative_prompt": "modern, smartphone, car, bicycle, suit, internet, skyscraper, neon light",
                }
            )

        # inject generic tags into prompts (not China-specific)
        setting = (era_profile.get("setting") or {}) if isinstance(era_profile, dict) else {}
        region_tag = str(setting.get("region") or "").strip()
        era_tag = str(setting.get("era") or setting.get("period") or "").strip()
        culture_tag = str(setting.get("culture_style") or "").strip()

        for i, sc in enumerate(script["scenes"], start=1):
            sc["id"] = i
            sc["media_type"] = (sc.get("media_type") or "image").lower()
            sc["importance"] = (sc.get("importance") or "normal").lower()
            sc["orientation"] = (sc.get("orientation") or "portrait").lower()
            sc["image_prompt"] = sc.get("image_prompt") or sc.get("video_prompt") or ""
            sc["video_prompt"] = sc.get("video_prompt") or sc.get("image_prompt") or ""
            sc["negative_prompt"] = sc.get("negative_prompt") or "modern, smartphone, car, bicycle, suit, internet, skyscraper, neon light"
            try:
                sc["seconds"] = int(sc.get("seconds", 15))
            except Exception:
                sc["seconds"] = 15
            if sc["seconds"] < 1:
                sc["seconds"] = 1

            tail = []
            if region_tag:
                tail.append(f"Region:{region_tag}")
            if era_tag:
                tail.append(f"Era:{era_tag}")
            if culture_tag:
                tail.append(f"Culture:{culture_tag}")
            if tail:
                suffix = " ".join(tail)
                sc["image_prompt"] += f" ({suffix})"
                sc["video_prompt"] += f" ({suffix})"

        # duration allocation
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
                external_media=self.external_media,
                category=category,
                # prefer inferred era_profile keywords if present, else old translation
                search_keywords_en=" ".join(((era_profile.get("search_keywords_en") or {}).get("required_terms") or [])) if isinstance(era_profile, dict) else search_keywords_en,
                reuse_cooldown_scenes=int(rules.get("reuse_cooldown_scenes") or 2),
                ffprobe_bin="ffprobe",
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
            assets={**assets, "era_profile": era_profile},
            final_video_path=final_video_path,
        )

        tracer.emit("run_end", record_id=rec_id)

        return {
            "record_id": rec_id,
            "series": series,
            "episode": episode,
            "category": category,
            "outline": outline,
            "era_profile": era_profile,
            "script": script,
            "assets": assets,
            "final_video_path": final_video_path,
            "export_dir": export_dir,
            "trace_path": os.path.join(run_dir, "trace.jsonl"),
        }