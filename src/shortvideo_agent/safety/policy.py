from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable
import yaml


BANNED_TOPICS = [
    "新闻", "时政", "政治", "选举", "政府", "政党", "外交", "战争", "冲突", "示威", "游行",
    "制裁", "恐怖袭击", "枪击", "爆炸", "突发", "快讯", "热点", "通报", "记者", "发布会",
    "某地发生", "今天", "刚刚", "最新", "实时",
    "breaking", "election", "government", "politics", "news",
]

BANNED_PATTERNS = [
    r"(?i)\b(Reuters|AP|BBC|CNN|Fox)\b",
    r"(?i)\bbreaking\s+news\b",
]


@dataclass(frozen=True)
class SafetyResult:
    ok: bool
    reason: str | None = None


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


def check_text_policy(text: str) -> SafetyResult:
    t = text.strip()
    for kw in BANNED_TOPICS:
        if kw and kw in t:
            return SafetyResult(False, f"Contains banned topic keyword: {kw}")
    for pat in BANNED_PATTERNS:
        if re.search(pat, t):
            return SafetyResult(False, f"Matches banned pattern: {pat}")
    return SafetyResult(True)