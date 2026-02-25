from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def find_args_file(project_dir: str) -> str | None:
    p = os.path.join(project_dir, ".args")
    return p if os.path.exists(p) else None


def parse_args_file(path: str) -> dict[str, str]:
    """
    支持格式：
      --key=value
      # 注释
      ; 注释（兼容中文习惯）
    """
    data: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith(";"):
            continue
        if not line.startswith("--"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if v == "":
            continue
        data[k] = v
    return data


def coerce_types(k: str, v: str) -> Any:
    """
    将常见类型转换：bool/int/float/str
    """
    lv = v.lower()
    if lv in ("true", "false"):
        return lv == "true"
    # int
    try:
        if v.isdigit() or (v.startswith("-") and v[1:].isdigit()):
            return int(v)
    except Exception:
        pass
    # float
    try:
        if "." in v:
            return float(v)
    except Exception:
        pass
    return v