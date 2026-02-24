from __future__ import annotations

import base64
from pathlib import Path
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential


class OpenAIImages:
    def __init__(self, *, api_key: str, base_url: str | None, model: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def generate_image(self, *, prompt: str, out_path: str, size: str = "1024x1024") -> str:
        # gpt-image-1 returns base64
        res = self.client.images.generate(
            model=self.model,
            prompt=prompt,
            size=size,
        )
        b64 = res.data[0].b64_json
        data = base64.b64decode(b64)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(data)
        return out_path