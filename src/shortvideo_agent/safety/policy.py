from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable
import yaml


# 关键词：用于“软命中”，不直接拒绝
BANNED_TOPICS = [
    "新闻", "时政", "政治", "选举", "政府", "政党", "外交", "战争", "冲突", "示威", "游行",
    "制裁", "恐怖袭击", "枪击", "爆炸", "突发", "快讯", "热点", "通报", "记者", "发布会",
    "某地发生", "今天", "刚刚", "最新", "实时",
    "breaking", "election", "government", "politics", "news",
]

# 高置信模式：可直接硬拒绝
BANNED_PATTERNS = [
    r"(?i)\b(Reuters|AP|BBC|CNN|Fox)\b",
    r"(?i)\bbreaking\s+news\b",
]


@dataclass(frozen=True)
class SafetyResult:
    ok: bool
    reason: str | None = None
    matched: list[str] | None = None
    matched_patterns: list[str] | None = None


def load_category_allowlist(categories_config_path: str) -> set[str]:
    if not os.path.exists(categories_config_path):
        raise RuntimeError(f"Missing categories config: {categories_config_path}")
    with open(categories_config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    allow = cfg.get("allowlist") or []
    return {str(x) for x in allow}


def check_category(category: str, allowlist: Iterable[str]) -> SafetyResult:
    allow = set(allowlist)
    if category not in allow:
        return SafetyResult(False, f"Category '{category}' not allowed. Allowed: {sorted(allow)}")
    return SafetyResult(True)


def soft_match(text: str) -> SafetyResult:
    """
    软命中：仅收集关键词/模式，不做拒绝。
    """
    t = text.strip()
    matched = [kw for kw in BANNED_TOPICS if kw and kw in t]
    matched_patterns = [pat for pat in BANNED_PATTERNS if re.search(pat, t)]
    return SafetyResult(True, matched=matched, matched_patterns=matched_patterns)


def hard_block_if_patterns(text: str) -> SafetyResult:
    """
    高置信模式硬拒绝（可选保留）。
    """
    t = text.strip()
    for pat in BANNED_PATTERNS:
        if re.search(pat, t):
            return SafetyResult(False, f"Matches banned pattern: {pat}", matched_patterns=[pat])
    return SafetyResult(True)