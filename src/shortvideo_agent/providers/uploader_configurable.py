from __future__ import annotations

import os
from typing import Any, Iterable

import yaml

from .uploaders import (
    UploadTask,
    QueueCopyUploader,
    DouyinOpenAPIUploader,
    KuaishouOpenAPIUploader,
    BilibiliOpenAPIUploader,
    XiaohongshuOpenAPIUploader,
    ShipinhaoOpenAPIUploader,
)
from ..utils.files import ensure_dir


class ConfigurableBatchUploader:
    def __init__(self, *, upload_root: str, platforms_config_path: str, providers_config_path: str, token_dir: str) -> None:
        self.upload_root = upload_root
        ensure_dir(upload_root)

        self.platforms_cfg = self._load_yaml(platforms_config_path)
        self.providers_cfg = self._load_yaml(providers_config_path)

        self.platform_map: dict[str, dict[str, Any]] = {}
        for p in (self.platforms_cfg.get("platforms") or []):
            key = p.get("key")
            if key:
                self.platform_map[str(key)] = p

        self.platform_apis = (self.providers_cfg.get("platform_apis") or {})
        self.token_dir = token_dir
        ensure_dir(self.token_dir)

        self._uploader_cache: dict[str, Any] = {}

    def _load_yaml(self, path: str) -> dict[str, Any]:
        if not os.path.exists(path):
            raise RuntimeError(f"Missing config file: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def supported_platforms(self, *, enabled_only: bool = True) -> list[str]:
        out = []
        for key, cfg in self.platform_map.items():
            if enabled_only and not bool(cfg.get("enabled", True)):
                continue
            out.append(key)
        return sorted(out)

    def _get_uploader(self, platform: str):
        if platform in self._uploader_cache:
            return self._uploader_cache[platform]

        if platform not in self.platform_map:
            raise ValueError(
                f"Unknown platform: {platform}. Supported: {self.supported_platforms(enabled_only=False)}"
            )

        pcfg = self.platform_map[platform]
        uploader_type = pcfg.get("uploader", "queue_copy")
        api_cfg = self.platform_apis.get(platform, {}) or {}

        if uploader_type == "queue_copy":
            inst = QueueCopyUploader(upload_root=self.upload_root, queue_subdir=pcfg.get("queue_subdir", platform))
        elif uploader_type == "douyin_openapi":
            inst = DouyinOpenAPIUploader(api_cfg=api_cfg, token_dir=self.token_dir)
        elif uploader_type == "kuaishou_openapi":
            inst = KuaishouOpenAPIUploader(api_cfg=api_cfg, token_dir=self.token_dir)
        elif uploader_type == "bilibili_openapi":
            inst = BilibiliOpenAPIUploader(api_cfg=api_cfg, token_dir=self.token_dir)
        elif uploader_type == "xiaohongshu_openapi":
            inst = XiaohongshuOpenAPIUploader(api_cfg=api_cfg, token_dir=self.token_dir)
        elif uploader_type == "shipinhao_openapi":
            inst = ShipinhaoOpenAPIUploader(api_cfg=api_cfg, token_dir=self.token_dir)
        else:
            raise NotImplementedError(f"Unknown uploader type: {uploader_type}")

        self._uploader_cache[platform] = inst
        return inst

    def upload_many(self, tasks: Iterable[UploadTask]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for t in tasks:
            uploader = self._get_uploader(t.platform)
            results.append({"platform": t.platform, "result": uploader.upload(t)})
        return results