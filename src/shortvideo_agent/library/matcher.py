from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz

from .models import MediaItem


def tokenize_keywords(text: str) -> list[str]:
    # 轻量分词：按常见分隔符拆；你后续可换成更强分词/向量
    seps = ["，", ",", "。", ".", "；", ";", "、", "/", "|", "\n", "\t"]
    t = text
    for s in seps:
        t = t.replace(s, " ")
    toks = [x.strip().lower() for x in t.split(" ") if x.strip()]
    # 去重保序
    seen = set()
    out = []
    for x in toks:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out[:30]


@dataclass(frozen=True)
class MatchResult:
    item: MediaItem
    score: float


def match_media(
    *,
    query_keywords: Iterable[str],
    query_prompt: str,
    candidates: list[MediaItem],
    min_score: float = 70.0,
) -> MatchResult | None:
    qk = {k.lower() for k in query_keywords if k}
    best: MatchResult | None = None

    for it in candidates:
        ik = {k.lower() for k in it.keywords if k}
        # keyword jaccard
        inter = len(qk & ik)
        union = max(1, len(qk | ik))
        jacc = 100.0 * inter / union

        # prompt similarity
        ps = float(fuzz.token_set_ratio(query_prompt, it.prompt))

        # weighted score
        score = 0.55 * ps + 0.45 * jacc

        if score >= min_score and (best is None or score > best.score):
            best = MatchResult(item=it, score=score)

    return best