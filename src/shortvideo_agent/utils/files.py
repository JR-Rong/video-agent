from __future__ import annotations

import os
import shutil
from pathlib import Path


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def write_text(path: str, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")


def copy_file(src: str, dst: str) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)