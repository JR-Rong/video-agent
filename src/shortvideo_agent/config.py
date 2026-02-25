from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv
import yaml


@dataclass(frozen=True)
class Settings:
    # secrets/env
    deepseek_api_key: str | None
    deepseek_base_url: str | None
    deepseek_model: str | None

    qwen_api_key: str | None
    qwen_base_url: str | None
    qwen_model: str | None

    openai_api_key: str | None
    openai_base_url: str | None
    openai_image_model: str | None

    kling_access_key: str | None
    kling_secret_key: str | None
    kling_base_url: str | None

    # paths
    data_dir: str
    output_dir: str
    export_dir: str
    media_library_dir: str
    strict_safety: bool

    # config paths
    providers_config_path: str
    categories_config_path: str
    output_routing_config_path: str
    render_presets_config_path: str
    prompts_dir: str

    # render/tts
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

    providers_config_path = _getenv("PROVIDERS_CONFIG", "./configs/providers.yaml") or "./configs/providers.yaml"
    categories_config_path = _getenv("CATEGORIES_CONFIG", "./configs/categories.yaml") or "./configs/categories.yaml"
    output_routing_config_path = _getenv("OUTPUT_ROUTING_CONFIG", "./configs/output_routing.yaml") or "./configs/output_routing.yaml"
    render_presets_config_path = _getenv("RENDER_PRESETS_CONFIG", "./configs/render_presets.yaml") or "./configs/render_presets.yaml"
    prompts_dir = _getenv("PROMPTS_DIR", "./prompts") or "./prompts"

    data_dir = _getenv("DATA_DIR", "./data") or "./data"
    output_dir = _getenv("OUTPUT_DIR", "./data/outputs") or "./data/outputs"
    export_dir = _getenv("EXPORT_DIR", "./data/export") or "./data/export"
    media_library_dir = _getenv("MEDIA_LIBRARY_DIR", "./data/media_library") or "./data/media_library"

    providers = _load_yaml(providers_config_path)
    tts_cfg = (providers.get("tts") or {})
    render_cfg = (providers.get("render") or {})

    return Settings(
        deepseek_api_key=_getenv("DEEPSEEK_API_KEY", None),
        deepseek_base_url=_getenv("DEEPSEEK_BASE_URL", None),
        deepseek_model=_getenv("DEEPSEEK_MODEL", None),
        qwen_api_key=_getenv("QWEN_API_KEY", None),
        qwen_base_url=_getenv("QWEN_BASE_URL", None),
        qwen_model=_getenv("QWEN_MODEL", None),
        openai_api_key=_getenv("OPENAI_API_KEY", None),
        openai_base_url=_getenv("OPENAI_BASE_URL", None),
        openai_image_model=_getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
        kling_access_key=_getenv("KLING_ACCESS_KEY", None),
        kling_secret_key=_getenv("KLING_SECRET_KEY", None),
        kling_base_url=_getenv("KLING_BASE_URL", None),
        data_dir=data_dir,
        output_dir=output_dir,
        export_dir=export_dir,
        media_library_dir=media_library_dir,
        strict_safety=strict,
        providers_config_path=providers_config_path,
        categories_config_path=categories_config_path,
        output_routing_config_path=output_routing_config_path,
        render_presets_config_path=render_presets_config_path,
        prompts_dir=prompts_dir,
        tts_provider=_getenv("TTS_PROVIDER", tts_cfg.get("provider") or "edge_tts") or "edge_tts",
        tts_voice=_getenv("TTS_VOICE", tts_cfg.get("voice") or "zh-CN-XiaoxiaoNeural") or "zh-CN-XiaoxiaoNeural",
        ffmpeg_bin=_getenv("FFMPEG_BIN", render_cfg.get("ffmpeg_bin") or "ffmpeg") or "ffmpeg",
        render_preset=_getenv("RENDER_PRESET", render_cfg.get("preset") or "vertical_1080x1920") or "vertical_1080x1920",
    )