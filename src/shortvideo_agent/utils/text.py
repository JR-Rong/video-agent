from __future__ import annotations

import re


def clamp_int(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "output"