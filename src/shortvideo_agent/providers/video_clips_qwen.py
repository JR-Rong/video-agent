from __future__ import annotations

import base64
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential


class QwenVideoClips:
    """
    DashScope wan video synthesis (async HTTP):
    POST {base}/services/aigc/video-generation/video-synthesis   with header X-DashScope-Async: enable
    GET  {base}/tasks/{task_id}
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/api/v1",
        model_t2v: str = "wanx2.1-t2v-turbo",
        model_i2v: str = "wan2.6-i2v-flash",
        timeout_sec: int = 120,
        poll_interval_sec: float = 15.0,
        poll_timeout_sec: int = 900,
    ) -> None:
        if not api_key:
            raise RuntimeError("Missing DASHSCOPE_API_KEY for QwenVideoClips")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_t2v = model_t2v
        self.model_i2v = model_i2v
        self.timeout = timeout_sec
        self.poll_interval = poll_interval_sec
        self.poll_timeout = poll_timeout_sec

    def _headers_async(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _download(self, url: str, out_path: str) -> str:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        Path(out_path).write_bytes(r.content)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("Downloaded Qwen video is empty.")
        return out_path

    def _poll_task(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout
        last: Optional[dict[str, Any]] = None
        while time.time() < deadline:
            url = f"{self.base_url}/tasks/{task_id}"
            r = requests.get(url, headers=self._headers(), timeout=self.timeout)
            if r.status_code >= 400:
                raise RuntimeError(f"Qwen task GET {r.status_code}: {r.text}")
            data = r.json()
            last = data
            status = (((data.get("output") or {}).get("task_status")) or "").upper()
            if status == "SUCCEEDED":
                return data
            if status in ("FAILED", "CANCELED"):
                raise RuntimeError(f"Qwen task failed: {data}")
            time.sleep(self.poll_interval)
        raise RuntimeError(f"Qwen task poll timeout. last={last}")

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=8), reraise=True)
    def generate_text2video(self, *, prompt: str, out_path: str, size: str = "720*1280", duration: int = 5, negative_prompt: str | None = None, prompt_extend: bool = True) -> str:
        url = f"{self.base_url}/services/aigc/video-generation/video-synthesis"
        payload: dict[str, Any] = {
            "model": self.model_t2v,
            "input": {"prompt": prompt},
            "parameters": {
                "size": size,
                "duration": int(duration),
                "prompt_extend": bool(prompt_extend),
            },
        }
        if negative_prompt:
            payload["input"]["negative_prompt"] = negative_prompt

        r = requests.post(url, headers=self._headers_async(), json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Qwen t2v POST {r.status_code}: {r.text}")
        data = r.json()
        task_id = (data.get("output") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"Qwen t2v missing task_id: {data}")

        done = self._poll_task(task_id)
        video_url = (done.get("output") or {}).get("video_url")
        if not video_url:
            raise RuntimeError(f"Qwen t2v missing video_url: {done}")
        return self._download(video_url, out_path)

    def _encode_image_data_url(self, image_path: str) -> str:
        mime, _ = mimetypes.guess_type(image_path)
        if not mime:
            mime = "image/png"
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{b64}"

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=8), reraise=True)
    def generate_image2video(
        self,
        *,
        prompt: str,
        image_path: str,
        out_path: str,
        resolution: str = "720P",
        duration: int = 5,
        negative_prompt: str | None = None,
        prompt_extend: bool = True,
        audio: bool | None = None,
    ) -> str:
        url = f"{self.base_url}/services/aigc/video-generation/video-synthesis"
        img_url = self._encode_image_data_url(image_path)
        payload: dict[str, Any] = {
            "model": self.model_i2v,
            "input": {
                "prompt": prompt,
                "img_url": img_url,
            },
            "parameters": {
                "resolution": resolution,
                "duration": int(duration),
                "prompt_extend": bool(prompt_extend),
                "shot_type": "single",
            },
        }
        if negative_prompt:
            payload["input"]["negative_prompt"] = negative_prompt
        if audio is not None:
            payload["parameters"]["audio"] = bool(audio)

        r = requests.post(url, headers=self._headers_async(), json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Qwen i2v POST {r.status_code}: {r.text}")
        data = r.json()
        task_id = (data.get("output") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"Qwen i2v missing task_id: {data}")

        done = self._poll_task(task_id)
        video_url = (done.get("output") or {}).get("video_url")
        if not video_url:
            raise RuntimeError(f"Qwen i2v missing video_url: {done}")
        return self._download(video_url, out_path)