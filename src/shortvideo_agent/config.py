from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv
import yaml


@dataclass(frozen=True)
class Settings:
    openai_api_key: str

    data_dir: str
    output_dir: str
    export_dir: str
    strict_safety: bool

    platforms_config_path: str | None
    providers_config_path: str
    categories_config_path: str
    output_routing_config_path: str
    render_presets_config_path: str
    prompts_dir: str

    openai_base_url: str | None
    openai_text_model: str
    openai_image_model: str
    openai_video_model: str | None

    tts_provider: str
    tts_voice: str

    ffmpeg_bin: str
    render_preset: str


def _getenv(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    return v if (v is not None and v != "") else default


def _load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        raise RuntimeError(f"Missing config file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    load_dotenv()

    strict = (_getenv("STRICT_SAFETY", "true") or "true").lower() in ("1", "true", "yes")
    api_key = _getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in environment (.env).")

    data_dir = _getenv("DATA_DIR", "./data") or "./data"
    output_dir = _getenv("OUTPUT_DIR", "./data/outputs") or "./data/outputs"
    export_dir = _getenv("EXPORT_DIR", "./data/export") or "./data/export"

    providers_config_path = _getenv("PROVIDERS_CONFIG", "./configs/providers.yaml") or "./configs/providers.yaml"
    categories_config_path = _getenv("CATEGORIES_CONFIG", "./configs/categories.yaml") or "./configs/categories.yaml"
    output_routing_config_path = _getenv("OUTPUT_ROUTING_CONFIG", "./configs/output_routing.yaml") or "./configs/output_routing.yaml"
    render_presets_config_path = _getenv("RENDER_PRESETS_CONFIG", "./configs/render_presets.yaml") or "./configs/render_presets.yaml"
    prompts_dir = _getenv("PROMPTS_DIR", "./prompts") or "./prompts"

    providers = _load_yaml(providers_config_path)
    openai_cfg = (providers.get("openai") or {})
    tts_cfg = (providers.get("tts") or {})
    render_cfg = (providers.get("render") or {})

    openai_base_url = _getenv("OPENAI_BASE_URL", openai_cfg.get("base_url") or None)
    text_model = _getenv("OPENAI_TEXT_MODEL", openai_cfg.get("text_model") or "gpt-4o-mini") or "gpt-4o-mini"
    image_model = _getenv("OPENAI_IMAGE_MODEL", openai_cfg.get("image_model") or "gpt-image-1") or "gpt-image-1"
    video_model = _getenv("OPENAI_VIDEO_MODEL", openai_cfg.get("video_model") or "") or ""
    video_model = video_model if video_model.strip() else None

    tts_provider = _getenv("TTS_PROVIDER", tts_cfg.get("provider") or "edge_tts") or "edge_tts"
    tts_voice = _getenv("TTS_VOICE", tts_cfg.get("voice") or "zh-CN-XiaoxiaoNeural") or "zh-CN-XiaoxiaoNeural"

    ffmpeg_bin = _getenv("FFMPEG_BIN", render_cfg.get("ffmpeg_bin") or "ffmpeg") or "ffmpeg"
    render_preset = _getenv("RENDER_PRESET", render_cfg.get("preset") or "vertical_1080x1920") or "vertical_1080x1920"

    return Settings(
        openai_api_key=api_key,
        data_dir=data_dir,
        output_dir=output_dir,
        export_dir=export_dir,
        strict_safety=strict,
        platforms_config_path=None,
        providers_config_path=providers_config_path,
        categories_config_path=categories_config_path,
        output_routing_config_path=output_routing_config_path,
        render_presets_config_path=render_presets_config_path,
        prompts_dir=prompts_dir,
        openai_base_url=openai_base_url,
        openai_text_model=text_model,
        openai_image_model=image_model,
        openai_video_model=video_model,
        tts_provider=tts_provider,
        tts_voice=tts_voice,
        ffmpeg_bin=ffmpeg_bin,
        render_preset=render_preset,
    )