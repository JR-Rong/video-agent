from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from ..utils.files import ensure_dir, copy_file


@dataclass(frozen=True)
class UploadTask:
    platform: str
    title: str
    description: str
    video_path: str


class BatchUploaderStub:
    """
    各平台真正上传需要：
    - 抖音/快手/B站/小红书等：各自开放平台 + OAuth/签名/素材上传接口
    - 或浏览器自动化（不推荐，易失效）

    这里提供可扩展的接口：先把文件复制到 data/uploads/{platform}/ 作为“待上传队列”。
    """

    def __init__(self, upload_root: str) -> None:
        self.upload_root = upload_root
        ensure_dir(upload_root)

    def upload_many(self, tasks: Iterable[UploadTask]) -> list[str]:
        results: list[str] = []
        for t in tasks:
            platform_dir = os.path.join(self.upload_root, t.platform)
            ensure_dir(platform_dir)
            dst = os.path.join(platform_dir, os.path.basename(t.video_path))
            copy_file(t.video_path, dst)
            results.append(dst)
        return results