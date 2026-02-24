from __future__ import annotations

from pathlib import Path
from typing import Any


def load_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def render_template(template: str, ctx: dict[str, Any]) -> str:
    # 极简 {{var}} 替换（避免引入 jinja2 降低依赖成本）
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v if v is not None else ""))
    return out