from __future__ import annotations

import os
from typing import Any
import yaml

from ...config import Settings
from ..external_media.base import ExternalMediaCandidate
from ..external_media.pexels import PexelsClient
from ..external_media.pixabay import PixabayClient
from ..external_media.archive_org import ArchiveOrgClient


class ExternalMediaRouter:
    def __init__(
        self,
        *,
        enabled: bool,
        providers: list[str],
        cfg: dict[str, Any],
        pexels: PexelsClient | None,
        pixabay: PixabayClient | None,
        archive: ArchiveOrgClient | None,
    ) -> None:
        self.enabled = enabled
        self.providers = providers
        self.cfg = cfg
        self.pexels = pexels
        self.pixabay = pixabay
        self.archive = archive

    def _per_query(self) -> int:
        return int((self.cfg.get("external_search") or {}).get("per_query") or 8)

    def search_video(self, *, query: str, orientation: str) -> list[ExternalMediaCandidate]:
        if not self.enabled:
            return []
        per_query = self._per_query()
        for p in self.providers:
            try:
                if p == "pexels" and self.pexels:
                    res = self.pexels.search_videos(query=query, per_page=per_query, orientation=orientation)
                elif p == "pixabay" and self.pixabay:
                    res = self.pixabay.search_videos(query=query, per_page=per_query)
                elif p == "archive" and self.archive:
                    res = self.archive.search_videos(query=query, per_page=min(5, per_query))
                else:
                    res = []
            except Exception:
                res = []
            if res:
                return res
        return []

    def search_image(self, *, query: str, orientation: str) -> list[ExternalMediaCandidate]:
        if not self.enabled:
            return []
        per_query = self._per_query()
        for p in self.providers:
            try:
                if p == "pexels" and self.pexels:
                    res = self.pexels.search_images(query=query, per_page=per_query, orientation=orientation)
                elif p == "pixabay" and self.pixabay:
                    ori = "vertical" if orientation == "portrait" else "horizontal"
                    res = self.pixabay.search_images(query=query, per_page=per_query, orientation=ori)
                elif p == "archive":
                    res = []
                else:
                    res = []
            except Exception:
                res = []
            if res:
                return res
        return []

    def download(self, *, cand: ExternalMediaCandidate, out_path: str) -> str:
        if cand.provider == "pexels" and self.pexels:
            return self.pexels.download(url=cand.download_url, out_path=out_path)
        if cand.provider == "pixabay" and self.pixabay:
            return self.pixabay.download(url=cand.download_url, out_path=out_path)

        import requests
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with requests.get(cand.download_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return out_path


def build_external_media(*, settings: Settings) -> ExternalMediaRouter:
    with open(settings.providers_config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    es = cfg.get("external_search") or {}
    enabled = bool(es.get("enabled", False))
    providers = [str(x).lower() for x in (es.get("providers") or [])]
    if not providers:
        providers = ["pexels", "pixabay", "archive"]

    pexels = None
    pixabay = None
    archive = None

    if enabled:
        pex_key = os.getenv("PEXELS_API_KEY", "") or ""
        if pex_key:
            pexels = PexelsClient(api_key=pex_key)

        pix_key = os.getenv("PIXABAY_API_KEY", "") or ""
        if pix_key:
            pixabay = PixabayClient(api_key=pix_key)

        archive = ArchiveOrgClient()

    return ExternalMediaRouter(
        enabled=enabled,
        providers=providers,
        cfg=cfg,
        pexels=pexels,
        pixabay=pixabay,
        archive=archive,
    )