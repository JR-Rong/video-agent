from __future__ import annotations
from pathlib import Path
from typing import Any

def load_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")

def render_template(template: str, ctx: dict[str, Any]) -> str:
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v if v is not None else ""))
    return out