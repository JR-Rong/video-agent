from __future__ import annotations

import json
from typing import Any
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential


class OpenAILLM:
    def __init__(self, *, api_key: str, base_url: str | None, model: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def json_generate(self, *, system: str, user: str, schema_hint: str) -> dict[str, Any]:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user + "\n\n请只输出严格 JSON，不要输出任何多余文本。\nJSON结构提示:\n" + schema_hint},
            ],
            temperature=0.8,
        )
        text = resp.choices[0].message.content or "{}"
        return json.loads(text)