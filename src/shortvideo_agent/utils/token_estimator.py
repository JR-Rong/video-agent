from __future__ import annotations

from dataclasses import dataclass

try:
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    mode: str  # "provider" | "estimate"


def _count_tokens_tiktoken(text: str, model: str | None = None) -> int:
    if not text:
        return 0
    if tiktoken is None:
        # very rough fallback: 1 token ~= 4 chars for english, ~= 1.5~2 chars for chinese
        # We choose conservative: 1 token ~= 3 chars
        return max(1, len(text) // 3)
    try:
        enc = tiktoken.encoding_for_model(model or "gpt-4o-mini")
    except Exception:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def estimate_chat_tokens(*, system: str, user: str, completion: str, model: str | None = None) -> TokenUsage:
    pt = _count_tokens_tiktoken(system or "", model=model) + _count_tokens_tiktoken(user or "", model=model)
    ct = _count_tokens_tiktoken(completion or "", model=model)
    return TokenUsage(prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct, mode="estimate")