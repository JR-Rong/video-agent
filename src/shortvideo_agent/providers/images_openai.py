from __future__ import annotations

import base64
from pathlib import Path

from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception


def _normalize_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    b = base_url.strip()
    return b if b else None


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500
    if isinstance(exc, RateLimitError):
        if "insufficient_quota" in str(exc):
            return False
        return True
    return False


class OpenAIImages:
    def __init__(self, *, api_key: str, base_url: str | None, model: str) -> None:
        self.model = model
        bu = _normalize_base_url(base_url)
        self.client = OpenAI(api_key=api_key) if bu is None else OpenAI(api_key=api_key, base_url=bu)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        retry=retry_if_exception(_should_retry),
        reraise=True,
    )
    def generate_image(self, *, prompt: str, out_path: str, size: str = "1024x1024") -> str:
        try:
            res = self.client.images.generate(model=self.model, prompt=prompt, size=size)
        except RateLimitError as e:
            if "insufficient_quota" in str(e):
                raise RuntimeError(
                    "OpenAI 配额不足(insufficient_quota)，无法生成图片。请充值/启用计费或更换 Key。原始错误："
                    + str(e)
                ) from e
            raise

        b64 = res.data[0].b64_json
        data = base64.b64decode(b64)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(data)
        return out_path