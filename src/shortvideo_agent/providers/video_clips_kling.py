from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import jwt
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

class KlingInsufficientBalance(RuntimeError):
    pass

@dataclass
class KlingAuth:
    access_key: str
    secret_key: str
    token: str | None = None
    token_exp: int | None = None  # epoch seconds


class KlingClient:
    def __init__(
        self,
        *,
        base_url: str,
        access_key: str,
        secret_key: str,
        timeout_sec: int = 120,
    ) -> None:
        if not base_url:
            raise RuntimeError("Kling base_url is empty")
        if not access_key or not secret_key:
            raise RuntimeError("Kling access_key/secret_key is empty")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_sec
        self.auth = KlingAuth(access_key=access_key, secret_key=secret_key)

    def _ensure_token(self) -> str:
        now = int(time.time())
        # refresh if missing or expiring in 60s
        if self.auth.token and self.auth.token_exp and self.auth.token_exp - now > 60:
            return self.auth.token

        headers = {"alg": "HS256", "typ": "JWT"}
        exp = now + 1800
        payload = {"iss": self.auth.access_key, "exp": exp, "nbf": now - 5}
        token = jwt.encode(payload, self.auth.secret_key, headers=headers)
        self.auth.token = token
        self.auth.token_exp = exp
        return token

    def _headers(self) -> dict[str, str]:
        token = self._ensure_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
        if r.status_code == 429 and "Account balance not enough" in r.text:
            raise KlingInsufficientBalance(r.text)
        if r.status_code >= 400:
            raise RuntimeError(f"Kling POST {path} failed: {r.status_code} {r.text}")
        return r.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Kling GET {path} failed: {r.status_code} {r.text}")
        return r.json()

    def download(self, url: str, out_path: str) -> str:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=self.timeout) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("Downloaded file is empty.")
        return out_path


class KlingVideoClips:
    """
    Supports:
    - text2video: POST /v1/videos/text2video, GET /v1/videos/text2video/{task_id}
    - image2video: POST /v1/videos/image2video, GET /v1/videos/image2video/{task_id}
    - costs: GET /account/costs
    """

    def __init__(
        self,
        *,
        base_url: str,
        access_key: str,
        secret_key: str,
        model_name: str = "kling-v1",
        mode: str = "std",
        sound: str = "off",
        watermark_enabled: bool = False,
        timeout_sec: int = 120,
        poll_interval_sec: float = 2.0,
        poll_timeout_sec: int = 600,
    ) -> None:
        self.client = KlingClient(base_url=base_url, access_key=access_key, secret_key=secret_key, timeout_sec=timeout_sec)
        self.model_name = model_name
        self.mode = mode
        self.sound = sound
        self.watermark_enabled = watermark_enabled
        self.poll_interval = poll_interval_sec
        self.poll_timeout = poll_timeout_sec

    def _aspect_ratio(self, width: int, height: int) -> str:
        if width == height:
            return "1:1"
        return "9:16" if height > width else "16:9"

    def _duration_enum(self, seconds: int) -> str:
        return "10" if seconds >= 8 else "5"

    def _poll(self, *, get_path: str) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout
        last: Optional[dict[str, Any]] = None
        while time.time() < deadline:
            resp = self.client.get_json(get_path)
            last = resp
            if resp.get("code") != 0:
                raise RuntimeError(f"Kling poll error: {resp}")
            data = resp.get("data") or {}
            status = (data.get("task_status") or "").lower()
            if status == "succeed":
                return data
            if status == "failed":
                raise RuntimeError(f"Kling task failed: {data.get('task_status_msg') or data}")
            time.sleep(self.poll_interval)
        raise RuntimeError(f"Kling poll timeout. last={last}")

    def _extract_video_url(self, data: dict[str, Any]) -> str:
        videos = ((data.get("task_result") or {}).get("videos") or [])
        if not videos:
            raise RuntimeError(f"Kling succeed but missing videos: {data}")
        v0 = videos[0]
        # prefer no-watermark url
        url = v0.get("url") or v0.get("watermark_url")
        if not url:
            raise RuntimeError(f"Kling missing video url: {v0}")
        return str(url)

    # ---------- text2video ----------
    def generate_text2video(
        self,
        *,
        prompt: str,
        negative_prompt: str | None,
        seconds: int,
        width: int,
        height: int,
        out_path: str,
        mode: str | None = None,
        sound: str | None = None,
        model_name: str | None = None,
        watermark_enabled: bool | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model_name": model_name or self.model_name,
            "prompt": prompt,
            "negative_prompt": negative_prompt or "",
            "duration": self._duration_enum(seconds),
            "mode": mode or self.mode,
            "sound": sound or self.sound,
            "aspect_ratio": self._aspect_ratio(width, height),
            "watermark_info": {"enabled": bool(self.watermark_enabled if watermark_enabled is None else watermark_enabled)},
            "callback_url": "",
            "external_task_id": "",
        }
        resp = self.client.post_json("/v1/videos/text2video", payload)
        if resp.get("code") != 0:
            raise RuntimeError(f"Kling submit text2video error: {resp}")
        task_id = (resp.get("data") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"Kling submit missing task_id: {resp}")

        data = self._poll(get_path=f"/v1/videos/text2video/{task_id}")
        url = self._extract_video_url(data)
        return self.client.download(url, out_path)

    # ---------- image2video ----------
    def _image_to_base64(self, image_path: str) -> str:
        b = Path(image_path).read_bytes()
        return base64.b64encode(b).decode("utf-8")

    def generate_image2video(
        self,
        *,
        image: str,  # base64(no prefix) or URL
        image_tail: str | None,
        prompt: str | None,
        negative_prompt: str | None,
        seconds: int,
        width: int,
        height: int,
        out_path: str,
        mode: str | None = None,
        sound: str | None = None,
        model_name: str | None = None,
        watermark_enabled: bool | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model_name": model_name or self.model_name,
            "image": image,
            "duration": self._duration_enum(seconds),
            "mode": mode or self.mode,
            "sound": sound or self.sound,
            "watermark_info": {"enabled": bool(self.watermark_enabled if watermark_enabled is None else watermark_enabled)},
            "callback_url": "",
            "external_task_id": "",
        }
        if image_tail:
            payload["image_tail"] = image_tail
        if prompt is not None:
            payload["prompt"] = prompt
        if negative_prompt is not None:
            payload["negative_prompt"] = negative_prompt

        resp = self.client.post_json("/v1/videos/image2video", payload)
        if resp.get("code") != 0:
            raise RuntimeError(f"Kling submit image2video error: {resp}")
        task_id = (resp.get("data") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"Kling submit missing task_id: {resp}")

        data = self._poll(get_path=f"/v1/videos/image2video/{task_id}")
        url = self._extract_video_url(data)
        return self.client.download(url, out_path)

    # ---------- costs ----------
    def get_costs(self, *, start_time_ms: int, end_time_ms: int, resource_pack_name: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"start_time": start_time_ms, "end_time": end_time_ms}
        if resource_pack_name:
            params["resource_pack_name"] = resource_pack_name
        resp = self.client.get_json("/account/costs", params=params)
        if resp.get("code") != 0:
            raise RuntimeError(f"Kling costs error: {resp}")
        return resp.get("data") or {}

    # ---------- unified interface used by renderer ----------
    def generate_clip(
        self,
        *,
        prompt: str,
        seconds: int,
        out_path: str,
        width: int,
        height: int,
        fps: int = 24,  # Kling 接口不需要 fps，这里保留签名兼容
        negative_prompt: str | None = None,
        reference_image_base64: str | None = None,
    ) -> str:
        """
        渲染层统一调用入口：
        - 如果给了 reference_image_base64：走 image2video（更稳、更省，且可复用图片库）
        - 否则走 text2video
        """
        if reference_image_base64:
            return self.generate_image2video(
                image=reference_image_base64,
                image_tail=None,
                prompt=prompt or None,
                negative_prompt=negative_prompt,
                seconds=seconds,
                width=width,
                height=height,
                out_path=out_path,
            )
        return self.generate_text2video(
            prompt=prompt,
            negative_prompt=negative_prompt,
            seconds=seconds,
            width=width,
            height=height,
            out_path=out_path,
        )