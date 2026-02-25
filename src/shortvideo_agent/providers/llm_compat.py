from __future__ import annotations

import json
from typing import Any, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential


def _must_be_pure_json_object(text: str) -> dict[str, Any]:
    t = (text or "").strip()

    # 强要求：只允许一个 JSON object，不允许任何多余字符
    if not (t.startswith("{") and t.endswith("}")):
        raise ValueError(f"LLM output is not pure JSON object. head={t[:80]!r}")

    # 强要求：必须可解析
    return json.loads(t)


class OpenAICompatChatLLM:
    """
    OpenAI-compatible /chat/completions (DeepSeek, DashScope compatible-mode).
    Strict mode: MUST return pure JSON only.
    """

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_sec: int = 120) -> None:
        if not base_url.startswith("http"):
            raise ValueError(f"Invalid base_url: {base_url}")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_sec

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    def json_generate(self, *, system: str, user: str, schema_hint: str) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"

        # 强约束：再次在 user 末尾加入“协议”
        strict_suffix = (
            "\n\n【输出协议-严格】\n"
            "你必须只输出一个JSON对象，要求：\n"
            "1) 输出必须以 { 开头，以 } 结尾。\n"
            "2) JSON之外不得包含任何字符（包括解释、标题、Markdown、代码块标记```、换行前后空白以外的内容）。\n"
            "3) 所有字段必须符合给定的 JSON 结构提示。\n"
            "4) 若无法满足，仍必须输出一个JSON对象：{\"error\":\"FORMAT_ERROR\"}。\n"
            "再次强调：只输出JSON。\n"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": user
                    + "\n\nJSON结构提示:\n"
                    + schema_hint
                    + strict_suffix,
                },
            ],
            "temperature": 0.4,  # 降低随机性，提高结构稳定性
        }

        r = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"LLM request failed: {r.status_code} {r.text}")

        data = r.json()
        content: Optional[str] = ((((data.get("choices") or [{}])[0]).get("message") or {}).get("content"))
        if not content:
            raise RuntimeError(f"LLM empty response: {data}")

        obj = _must_be_pure_json_object(content)

        # 如果模型返回了 FORMAT_ERROR，也算失败（用于触发重试/切换，并记录日志）
        if isinstance(obj, dict) and obj.get("error") == "FORMAT_ERROR":
            raise ValueError("LLM returned FORMAT_ERROR JSON sentinel.")

        return obj