from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Optional

import requests


def _encode_image_data_url(image_path: str) -> str:
    mime, _ = mimetypes.guess_type(image_path)
    if not mime:
        mime = "image/jpeg"
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _must_be_json_obj(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if not (t.startswith("{") and t.endswith("}")):
        raise RuntimeError(f"Vision judge not pure JSON: head={t[:120]!r}")
    return json.loads(t)


class OpenAICompatVisionJudge:
    """
    Works for any OpenAI-compatible /chat/completions endpoint that supports:
      messages[].content = [{"type":"text"...},{"type":"image_url"...}]
    (DashScope compatible-mode VL models often support this too.)
    """

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_sec: int = 60) -> None:
        if not base_url:
            raise RuntimeError("OpenAICompatVisionJudge missing base_url")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_sec

    def judge(self, *, system: str, user_text: str, image_path: str, schema_hint: str) -> dict[str, Any]:
        img = _encode_image_data_url(image_path)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text + "\n\nJSON结构提示:\n" + schema_hint},
                        {"type": "image_url", "image_url": {"url": img}},
                    ],
                },
            ],
            "temperature": 0.0,
        }

        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Vision judge failed: {r.status_code} {r.text}")

        data = r.json()
        content = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        return _must_be_json_obj(content)


class EraGuardRouter:
    def __init__(
        self,
        *,
        primary: Optional[OpenAICompatVisionJudge],
        fallback: Optional[OpenAICompatVisionJudge],
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def judge(self, *, system: str, user_text: str, image_path: str, schema_hint: str) -> dict[str, Any]:
        last: Exception | None = None
        if self.primary:
            try:
                return self.primary.judge(system=system, user_text=user_text, image_path=image_path, schema_hint=schema_hint)
            except Exception as e:
                last = e
        if self.fallback:
            try:
                return self.fallback.judge(system=system, user_text=user_text, image_path=image_path, schema_hint=schema_hint)
            except Exception as e:
                last = e
        raise RuntimeError(f"EraGuardRouter: all providers failed. last={last}") from last