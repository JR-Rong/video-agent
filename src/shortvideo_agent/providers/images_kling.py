from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from .video_clips_kling import KlingClient  # 复用同一套 JWT 鉴权与下载


class KlingImages:
    """
    Kling image generation:
    - POST /v1/images/generations
    - GET  /v1/images/generations/{task_id}
    """

    def __init__(
        self,
        *,
        base_url: str,
        access_key: str,
        secret_key: str,
        model_name: str = "kling-v1",
        watermark_enabled: bool = False,
        timeout_sec: int = 120,
        poll_interval_sec: float = 2.0,
        poll_timeout_sec: int = 300,
    ) -> None:
        self.client = KlingClient(base_url=base_url, access_key=access_key, secret_key=secret_key, timeout_sec=timeout_sec)
        self.model_name = model_name
        self.watermark_enabled = watermark_enabled
        self.poll_interval = poll_interval_sec
        self.poll_timeout = poll_timeout_sec

    def _poll(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout
        last: Optional[dict[str, Any]] = None
        while time.time() < deadline:
            resp = self.client.get_json(f"/v1/images/generations/{task_id}")
            last = resp
            if resp.get("code") != 0:
                raise RuntimeError(f"Kling image poll error: {resp}")
            data = resp.get("data") or {}
            status = (data.get("task_status") or "").lower()
            if status == "succeed":
                return data
            if status == "failed":
                raise RuntimeError(f"Kling image task failed: {data.get('task_status_msg') or data}")
            time.sleep(self.poll_interval)
        raise RuntimeError(f"Kling image poll timeout. last={last}")

    def _extract_image_url(self, data: dict[str, Any]) -> str:
        images = ((data.get("task_result") or {}).get("images") or [])
        if not images:
            raise RuntimeError(f"Kling image succeed but missing images: {data}")
        img0 = images[0]
        url = img0.get("url") or img0.get("watermark_url")
        if not url:
            raise RuntimeError(f"Kling image missing url: {img0}")
        return str(url)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    def generate(
        self,
        *,
        prompt: str,
        out_path: str,
        negative_prompt: str | None = None,
        aspect_ratio: str = "9:16",
        resolution: str = "1k",  # 1k|2k
        model_name: str | None = None,
        n: int = 1,
        watermark_enabled: bool | None = None,
        image_base64: str | None = None,  # 图生图可用；文档说明图生图不支持 negative_prompt
        image_reference: str | None = None,  # subject|face
        image_fidelity: float | None = None,
        human_fidelity: float | None = None,
    ) -> str:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)

        payload: dict[str, Any] = {
            "model_name": model_name or self.model_name,
            "prompt": prompt,
            "n": int(max(1, min(9, n))),
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "watermark_info": {"enabled": bool(self.watermark_enabled if watermark_enabled is None else watermark_enabled)},
            "callback_url": "",
            "external_task_id": "",
        }

        if image_base64:
            payload["image"] = image_base64
            if image_reference:
                payload["image_reference"] = image_reference
            if image_fidelity is not None:
                payload["image_fidelity"] = float(image_fidelity)
            if human_fidelity is not None:
                payload["human_fidelity"] = float(human_fidelity)
            # 图生图不支持 negative_prompt（按文档）
        else:
            if negative_prompt is not None:
                payload["negative_prompt"] = negative_prompt

        resp = self.client.post_json("/v1/images/generations", payload)
        if resp.get("code") != 0:
            raise RuntimeError(f"Kling image submit error: {resp}")
        task_id = (resp.get("data") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"Kling image submit missing task_id: {resp}")

        data = self._poll(str(task_id))
        url = self._extract_image_url(data)

        # download to out_path
        self.client.download(url, out_path)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("Downloaded image is empty.")
        return out_path