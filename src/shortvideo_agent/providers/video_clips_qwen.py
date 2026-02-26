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

    IMPORTANT:
    - DashScope wan i2v/t2v duration constraints often are: 2~15 seconds (varies by model).
      We implement auto-chunking when duration > 15.
    """

    MAX_DURATION = 15
    MIN_DURATION = 2

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
        ffmpeg_bin: str = "ffmpeg",
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
        self.ffmpeg_bin = ffmpeg_bin

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

    def _clamp_duration(self, duration: int) -> int:
        d = int(duration)
        if d < self.MIN_DURATION:
            return self.MIN_DURATION
        if d > self.MAX_DURATION:
            return self.MAX_DURATION
        return d

    def _concat_mp4(self, clip_paths: list[str], out_path: str) -> str:
        """
        concat demuxer, re-encode not needed if same codec/container.
        """
        from pathlib import Path
        import subprocess

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        list_file = str(Path(out_path).with_suffix(".concat.txt"))
        lines = []
        for p in clip_paths:
            ap = os.path.abspath(p).replace("'", "'\\''")
            lines.append(f"file '{ap}'")
        Path(list_file).write_text("\n".join(lines), encoding="utf-8")

        cmd = [self.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out_path]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        Path(list_file).unlink(missing_ok=True)
        if p.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {p.stdout}")
        return out_path

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=8), reraise=True)
    def generate_text2video(
        self,
        *,
        prompt: str,
        out_path: str,
        size: str = "720*1280",
        duration: int = 5,
        negative_prompt: str | None = None,
        prompt_extend: bool = True,
    ) -> str:
        """
        If duration > 15, auto chunk and concat.
        """
        duration = int(duration)
        if duration > self.MAX_DURATION:
            parts = []
            remain = duration
            idx = 1
            while remain > 0:
                d = min(self.MAX_DURATION, remain)
                d = max(self.MIN_DURATION, d)
                part_path = str(Path(out_path).with_suffix(f".part{idx:02d}.mp4"))
                parts.append(
                    self.generate_text2video(
                        prompt=prompt,
                        out_path=part_path,
                        size=size,
                        duration=d,
                        negative_prompt=negative_prompt,
                        prompt_extend=prompt_extend,
                    )
                )
                remain -= d
                idx += 1
            return self._concat_mp4(parts, out_path)

        url = f"{self.base_url}/services/aigc/video-generation/video-synthesis"
        payload: dict[str, Any] = {
            "model": self.model_t2v,
            "input": {"prompt": prompt},
            "parameters": {
                "size": size,
                "duration": self._clamp_duration(duration),
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
        """
        If duration > 15, auto chunk and concat.
        """
        duration = int(duration)
        if duration > self.MAX_DURATION:
            parts = []
            remain = duration
            idx = 1
            while remain > 0:
                d = min(self.MAX_DURATION, remain)
                d = max(self.MIN_DURATION, d)
                part_path = str(Path(out_path).with_suffix(f".part{idx:02d}.mp4"))
                parts.append(
                    self.generate_image2video(
                        prompt=prompt,
                        image_path=image_path,
                        out_path=part_path,
                        resolution=resolution,
                        duration=d,
                        negative_prompt=negative_prompt,
                        prompt_extend=prompt_extend,
                        audio=audio,
                    )
                )
                remain -= d
                idx += 1
            return self._concat_mp4(parts, out_path)

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
                "duration": self._clamp_duration(duration),
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