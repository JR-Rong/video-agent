from __future__ import annotations

from typing import Any
import os
import yaml
import time

from ..config import Settings
from ..utils.tracing import Tracer
from .images_openai import OpenAIImages
from .images_kling import KlingImages
from .images_qwen import QwenImages


class ImagesRouter:
    def __init__(
        self,
        *,
        provider: str,
        kling_images: KlingImages | None,
        qwen_images: QwenImages | None,
        openai_images: OpenAIImages | None,
        cfg: dict[str, Any],
    ) -> None:
        self.provider = provider
        self.kling_images = kling_images
        self.qwen_images = qwen_images
        self.openai_images = openai_images
        self.cfg = cfg

    def _qwen_size_from_orientation(self, orientation: str) -> str:
        return "928*1664" if orientation == "portrait" else "1664*928"

    def generate_image(
        self,
        *,
        prompt: str,
        out_path: str,
        size: str = "1024x1024",
        negative_prompt: str | None = None,
        orientation: str = "portrait",
        importance: str = "normal",
        tracer: Tracer | None = None,
        step: str = "",
        scene_id: int | None = None,
    ) -> str:
        ig = self.cfg.get("image_generation") or {}
        allow_openai_fallback = bool(ig.get("allow_openai_fallback", False))

        kling_cfg = ig.get("kling") or {}
        kling_model = kling_cfg.get("model_name") or "kling-v2-1"
        kling_resolution = kling_cfg.get("default_size") or "1k"
        if (importance or "").lower() == "key":
            kling_resolution = kling_cfg.get("key_resolution") or kling_resolution
        kling_aspect = kling_cfg.get("default_aspect_ratio") or ("9:16" if orientation == "portrait" else "16:9")
        watermark_enabled = bool(kling_cfg.get("watermark_enabled") or False)

        # 1) Kling
        if self.provider == "kling":
            if not self.kling_images:
                raise RuntimeError("image_generation.provider=kling but KLING_ACCESS_KEY/KLING_SECRET_KEY missing")
            t0 = time.time()
            if tracer:
                tracer.emit("image_try", step=step, provider="kling", model=kling_model, resolution=kling_resolution, aspect_ratio=kling_aspect, scene_id=scene_id, out_path=out_path)
            try:
                out = self.kling_images.generate(
                    prompt=prompt,
                    out_path=out_path,
                    negative_prompt=negative_prompt,
                    aspect_ratio=kling_aspect,
                    resolution=kling_resolution,
                    model_name=kling_model,
                    watermark_enabled=watermark_enabled,
                    n=1,
                )
                if tracer:
                    tracer.emit("image_ok", step=step, provider="kling", elapsed_ms=int((time.time()-t0)*1000), scene_id=scene_id, out_path=out_path)
                return out
            except Exception as e:
                if tracer:
                    tracer.emit("image_fail", step=step, provider="kling", elapsed_ms=int((time.time()-t0)*1000), error=str(e), scene_id=scene_id)
                # fallback to qwen/openai
                pass

        # 2) Qwen
        if self.qwen_images:
            t0 = time.time()
            qwen_size = self._qwen_size_from_orientation(orientation)
            if tracer:
                tracer.emit("image_try", step=step, provider="qwen", model=getattr(self.qwen_images, "model", None), size=qwen_size, scene_id=scene_id, out_path=out_path)
            try:
                out = self.qwen_images.generate(
                    prompt=prompt,
                    out_path=out_path,
                    negative_prompt=negative_prompt,
                    size=qwen_size,
                    prompt_extend=True,
                    watermark=False,
                )
                if tracer:
                    tracer.emit("image_ok", step=step, provider="qwen", elapsed_ms=int((time.time()-t0)*1000), scene_id=scene_id, out_path=out_path)
                return out
            except Exception as e:
                if tracer:
                    tracer.emit("image_fail", step=step, provider="qwen", elapsed_ms=int((time.time()-t0)*1000), error=str(e), scene_id=scene_id)
                pass

        # 3) OpenAI
        if allow_openai_fallback and self.openai_images:
            t0 = time.time()
            if tracer:
                tracer.emit("image_try", step=step, provider="openai", model=getattr(self.openai_images, "model", None), size=size, scene_id=scene_id, out_path=out_path)
            out = self.openai_images.generate_image(prompt=prompt, out_path=out_path, size=size)
            if tracer:
                tracer.emit("image_ok", step=step, provider="openai", elapsed_ms=int((time.time()-t0)*1000), scene_id=scene_id, out_path=out_path)
            return out

        raise RuntimeError(
            "No available image provider. Configure Kling balance or DASHSCOPE_API_KEY, "
            "or enable openai fallback in configs/providers.yaml"
        )


def build_images(*, settings: Settings) -> Any:
    with open(settings.providers_config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    ig = cfg.get("image_generation") or {}
    provider = (ig.get("provider") or "kling").lower()

    kling_images = None
    if settings.kling_access_key and settings.kling_secret_key:
        kling_base_cfg = (cfg.get("kling") or {})
        base_url = settings.kling_base_url or kling_base_cfg.get("base_url") or "https://api-beijing.klingai.com"

        kling_cfg = ig.get("kling") or {}
        kling_images = KlingImages(
            base_url=base_url,
            access_key=settings.kling_access_key,
            secret_key=settings.kling_secret_key,
            model_name=(kling_cfg.get("model_name") or "kling-v1"),
            watermark_enabled=bool(kling_cfg.get("watermark_enabled") or False),
            timeout_sec=int((kling_base_cfg.get("timeout_sec") or 120)),
            poll_interval_sec=float((kling_base_cfg.get("poll_interval_sec") or 2.0)),
            poll_timeout_sec=int((kling_base_cfg.get("poll_timeout_sec") or 300)),
        )

    # qwen images (from cfg, not hard-coded)
    qwen_images = None
    qwen_cfg = ig.get("qwen") or {}
    dash_key = os.getenv("DASHSCOPE_API_KEY", "") or ""
    dash_base = (qwen_cfg.get("base_url") or os.getenv("DASHSCOPE_BASE_URL", "") or "https://dashscope.aliyuncs.com/api/v1").strip()
    qwen_model = str(qwen_cfg.get("model") or "qwen-image-plus")
    qwen_timeout = int(qwen_cfg.get("timeout_sec") or 120)

    if dash_key:
        qwen_images = QwenImages(api_key=dash_key, base_url=dash_base, model=qwen_model, timeout_sec=qwen_timeout)

    openai_images = None
    openai_cfg = ig.get("openai") or {}
    if settings.openai_api_key:
        openai_model = str(openai_cfg.get("model") or settings.openai_image_model or "gpt-image-1")
        openai_images = OpenAIImages(api_key=settings.openai_api_key, base_url=settings.openai_base_url, model=openai_model)

    return ImagesRouter(provider=provider, kling_images=kling_images, qwen_images=qwen_images, openai_images=openai_images, cfg=cfg)