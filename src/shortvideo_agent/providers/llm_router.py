from __future__ import annotations

from typing import Any
import yaml
import time

from ..config import Settings
from ..utils.tracing import Tracer
from .llm_compat import OpenAICompatChatLLM


class LLMRouter:
    def __init__(self, chain: list[tuple[str, Any]]) -> None:
        self.chain = chain  # [(name, llm_instance)]

    def json_generate(self, *, system: str, user: str, schema_hint: str, tracer: Tracer | None = None, step: str = "") -> dict[str, Any]:
        last_err: Exception | None = None
        for name, llm in self.chain:
            t0 = time.time()
            if tracer:
                tracer.emit("llm_try", step=step, provider=name, model=getattr(llm, "model", None), base_url=getattr(llm, "base_url", None))
            try:
                out = llm.json_generate(system=system, user=user, schema_hint=schema_hint)
                if tracer:
                    tracer.emit("llm_ok", step=step, provider=name, model=getattr(llm, "model", None), elapsed_ms=int((time.time()-t0)*1000))
                return out
            except Exception as e:
                last_err = e
                if tracer:
                    tracer.emit("llm_fail", step=step, provider=name, model=getattr(llm, "model", None),
                                elapsed_ms=int((time.time()-t0)*1000), error=str(e), error_type=type(e).__name__)
                continue
        raise RuntimeError(f"All LLM providers failed. last_error={last_err}") from last_err


def build_llm(*, settings: Settings) -> Any:
    with open(settings.providers_config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    order = (cfg.get("llm") or {}).get("providers") or ["deepseek", "qwen"]
    chain: list[tuple[str, Any]] = []

    for name in order:
        if name == "deepseek":
            dc = cfg.get("deepseek") or {}
            key = settings.deepseek_api_key
            if not key:
                continue
            base = settings.deepseek_base_url or dc.get("base_url") or "https://api.deepseek.com"
            model = settings.deepseek_model or dc.get("model") or "deepseek-chat"
            chain.append(("deepseek", OpenAICompatChatLLM(base_url=base, api_key=key, model=model)))
        elif name == "qwen":
            qc = cfg.get("qwen") or {}
            key = settings.qwen_api_key
            if not key:
                continue
            base = settings.qwen_base_url or qc.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            model = settings.qwen_model or qc.get("model") or "qwen-plus"
            chain.append(("qwen", OpenAICompatChatLLM(base_url=base, api_key=key, model=model)))
        else:
            continue

    if not chain:
        raise RuntimeError("No LLM provider configured. Set DEEPSEEK_API_KEY and/or QWEN_API_KEY in .env")
    return LLMRouter(chain)