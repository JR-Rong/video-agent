from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import yaml


@dataclass(frozen=True)
class EraConstraints:
    era: str | None
    realism: bool
    constraints_text: str
    negative_text: str
    banned_words: set[str]


def load_era_rules(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_constraints(*, rules_cfg: dict[str, Any], era: str | None, realism: bool | None) -> EraConstraints:
    default = rules_cfg.get("default") or {}
    eras = rules_cfg.get("eras") or {}

    e = eras.get(era) if era else None
    merged = dict(default)
    if isinstance(e, dict):
        merged.update(e)

    final_realism = bool(merged.get("realism", True) if realism is None else realism)

    banned_obj = merged.get("banned_modern_objects") or []
    banned_words = set(merged.get("banned_modern_words") or [])
    for x in banned_obj:
        banned_words.add(str(x))

    # build text for LLM / prompts
    parts = []
    if era:
        parts.append(f"时代背景：{era}")
    if final_realism:
        parts.append("题材要求：非魔幻/非科幻，必须符合时代逻辑与常识，不得出现现代科技与现代社会元素。")
    allowed_tech = merged.get("allowed_tech") or []
    clothing = merged.get("clothing") or []
    architecture = merged.get("architecture") or []

    if allowed_tech:
        parts.append(f"可出现的技术/物件示例：{allowed_tech}")
    if clothing:
        parts.append(f"服饰风格示例：{clothing}")
    if architecture:
        parts.append(f"场景建筑示例：{architecture}")
    if banned_obj:
        parts.append(f"禁止出现的现代物件/元素：{banned_obj}")

    constraints_text = "\n".join(parts).strip() or "无额外时代约束"
    negative_text = "，".join(sorted(banned_words)) if banned_words else ""

    return EraConstraints(
        era=era,
        realism=final_realism,
        constraints_text=constraints_text,
        negative_text=negative_text,
        banned_words=banned_words,
    )


def scan_for_violations(text: str, banned_words: set[str]) -> list[str]:
    hits = []
    for w in banned_words:
        if w and w in text:
            hits.append(w)
    return sorted(set(hits))