from __future__ import annotations

from typing import Any, Protocol

from ..utils.templating import render_template, load_text


class JudgeLLM(Protocol):
    def json_generate(self, *, system: str, user: str, schema_hint: str, tracer=None, step: str = "") -> dict[str, Any]:
        ...


JUDGE_SCHEMA = """{
  "should_block": true,
  "reason": "string",
  "confidence": 0.0,
  "matched_terms": ["string"]
}"""


def judge_block(
    *,
    llm: JudgeLLM,
    prompt_md_path: str,
    text: str,
    matched_terms: list[str],
    matched_patterns: list[str],
    tracer=None,
    step: str,
) -> dict[str, Any]:
    """
    使用 prompts/safety_judge.md 进行裁决提示词渲染，然后调用 LLM 输出严格 JSON。
    """
    tpl = load_text(prompt_md_path)
    user = render_template(
        tpl,
        {
            "schema": JUDGE_SCHEMA,
            "matched_terms": matched_terms,
            "matched_patterns": matched_patterns,
            "text": text,
        },
    )

    # system 留空或给一个很短的系统约束即可（真正内容在 md）
    system = "你是内容安全裁决器。只输出严格JSON。"

    out = llm.json_generate(system=system, user=user, schema_hint=JUDGE_SCHEMA, tracer=tracer, step=step)

    out["should_block"] = bool(out.get("should_block", False))
    out["confidence"] = float(out.get("confidence", 0.0))
    if "matched_terms" not in out:
        out["matched_terms"] = matched_terms
    if not out.get("reason"):
        out["reason"] = "no_reason"
    return out