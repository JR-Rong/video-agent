from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential


class QwenImages:
    """
    Qwen-Image sync HTTP:
    POST {base}/services/aigc/multimodal-generation/generation
    base: https://dashscope.aliyuncs.com/api/v1  (Beijing)
    """

    def __init__(self, *, api_key: str, base_url: str = "https://dashscope.aliyuncs.com/api/v1", model: str = "qwen-image-plus", timeout_sec: int = 120) -> None:
        if not api_key:
            raise RuntimeError("Missing DASHSCOPE_API_KEY for QwenImages")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_sec

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    def generate(
        self,
        *,
        prompt: str,
        out_path: str,
        negative_prompt: str | None = None,
        size: str = "928*1664",  # 9:16
        prompt_extend: bool = True,
        watermark: bool = False,
    ) -> str:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        url = f"{self.base_url}/services/aigc/multimodal-generation/generation"

        payload: dict[str, Any] = {
            "model": self.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ]
            },
            "parameters": {
                "prompt_extend": bool(prompt_extend),
                "watermark": bool(watermark),
                "size": size,
            },
        }
        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt

        r = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Qwen image HTTP {r.status_code}: {r.text}")

        data = r.json()
        # sync interface returns image url in output.choices[0].message.content[0].image
        try:
            img_url = data["output"]["choices"][0]["message"]["content"][0]["image"]
        except Exception:
            raise RuntimeError(f"Qwen image response parse failed: {data}")

        # download
        resp = requests.get(img_url, timeout=self.timeout)
        resp.raise_for_status()
        Path(out_path).write_bytes(resp.content)

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("Downloaded Qwen image is empty.")
        return out_path