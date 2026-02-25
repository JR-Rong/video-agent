from __future__ import annotations

from typing import Optional, Protocol, Any
import os
import yaml

from ..config import Settings
from .video_clips_kling import KlingVideoClips, KlingInsufficientBalance
from .video_clips_qwen import QwenVideoClips


class VideoGenerator(Protocol):
    def generate_clip(
        self,
        *,
        prompt: str,
        seconds: int,
        out_path: str,
        width: int,
        height: int,
        fps: int,
        negative_prompt: str | None = None,
        reference_image_base64: str | None = None,
    ) -> str: ...

    def get_costs(self, *, start_time_ms: int, end_time_ms: int, resource_pack_name: str | None = None) -> dict[str, Any]: ...


class VideoRouter:
    def __init__(self, *, primary: Any, fallback: Any | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    def get_costs(self, *, start_time_ms: int, end_time_ms: int, resource_pack_name: str | None = None) -> dict[str, Any]:
        if hasattr(self.primary, "get_costs"):
            return self.primary.get_costs(start_time_ms=start_time_ms, end_time_ms=end_time_ms, resource_pack_name=resource_pack_name)
        return {"resource_pack_subscribe_infos": []}

    def generate_clip(self, **kwargs) -> str:
        try:
            return self.primary.generate_clip(**kwargs)
        except KlingInsufficientBalance:
            if not self.fallback:
                raise
            # Kling 余额不足时 fallback Qwen
            # 若有 reference_image_base64：我们需要落盘成图片给 qwen i2v
            ref_b64 = kwargs.pop("reference_image_base64", None)
            out_path = kwargs["out_path"]
            prompt = kwargs["prompt"]
            seconds = int(kwargs["seconds"])
            width = int(kwargs["width"])
            height = int(kwargs["height"])
            neg = kwargs.get("negative_prompt")

            if ref_b64:
                import base64
                from pathlib import Path
                # ref_b64 是纯 base64（kling格式），qwen i2v 需要 dataURL 或本地文件
                # 我们这里写到临时 png
                tmp_img = str(Path(out_path).with_suffix(".ref.png"))
                Path(tmp_img).write_bytes(base64.b64decode(ref_b64))
                # qwen i2v 以 resolution 选择 720P/1080P
                resolution = "1080P" if max(width, height) >= 1080 else "720P"
                return self.fallback.generate_image2video(
                    prompt=prompt,
                    image_path=tmp_img,
                    out_path=out_path,
                    resolution=resolution,
                    duration=seconds,
                    negative_prompt=neg,
                )
            else:
                # text2video
                size = f"{width}*{height}"
                return self.fallback.generate_text2video(prompt=prompt, out_path=out_path, size=size, duration=seconds, negative_prompt=neg)


def build_video_generator(*, settings: Settings) -> Optional[VideoGenerator]:
    with open(settings.providers_config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    vg = cfg.get("video_generation") or {}
    provider = (vg.get("provider") or "none").lower()
    if provider == "none":
        return None

    if provider == "kling":
        kc = cfg.get("kling") or {}
        base_url = settings.kling_base_url or kc.get("base_url") or "https://api-beijing.klingai.com"
        if not settings.kling_access_key or not settings.kling_secret_key:
            raise RuntimeError("Kling selected but missing KLING_ACCESS_KEY/KLING_SECRET_KEY in .env")

        kling = KlingVideoClips(
            base_url=base_url,
            access_key=settings.kling_access_key,
            secret_key=settings.kling_secret_key,
            model_name=kc.get("model_name") or "kling-v1",
            mode=kc.get("mode") or "std",
            sound=kc.get("sound") or "off",
            watermark_enabled=bool(kc.get("watermark_enabled") or False),
            timeout_sec=int(kc.get("timeout_sec") or 120),
            poll_interval_sec=float(kc.get("poll_interval_sec") or 2.0),
            poll_timeout_sec=int(kc.get("poll_timeout_sec") or 600),
        )

        # qwen fallback if configured
        dash_key = os.getenv("DASHSCOPE_API_KEY", "") or ""
        dash_base = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1") or "https://dashscope.aliyuncs.com/api/v1"
        qwen = QwenVideoClips(api_key=dash_key, base_url=dash_base) if dash_key else None

        return VideoRouter(primary=kling, fallback=qwen)

    raise NotImplementedError(f"video_generation.provider={provider} not implemented")