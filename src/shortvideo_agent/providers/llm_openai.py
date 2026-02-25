from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception


def _normalize_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    b = base_url.strip()
    return b if b else None


def _should_retry(exc: BaseException) -> bool:
    # 网络/超时：可重试
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True

    # HTTP 状态类错误：部分可重试
    if isinstance(exc, APIStatusError):
        # 5xx 可重试
        return exc.status_code >= 500

    # 429：区分可恢复限流 vs 配额不足
    if isinstance(exc, RateLimitError):
        # openai python 会把响应结构放在 exc.response / message 中
        # 我们用字符串兜底判断
        msg = str(exc)
        if "insufficient_quota" in msg:
            return False
        return True

    return False


class OpenAILLM:
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
    def json_generate(self, *, system: str, user: str, schema_hint: str) -> dict[str, Any]:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": user
                        + "\n\n请只输出严格 JSON，不要输出任何多余文本。\nJSON结构提示:\n"
                        + schema_hint,
                    },
                ],
                temperature=0.8,
            )
        except RateLimitError as e:
            if "insufficient_quota" in str(e):
                raise RuntimeError(
                    "OpenAI 配额不足(insufficient_quota)。请检查 OpenAI 账户是否已充值/启用计费，"
                    "或更换 OPENAI_API_KEY / OPENAI_BASE_URL。原始错误："
                    + str(e)
                ) from e
            raise

        text = resp.choices[0].message.content or "{}"
        return json.loads(text)