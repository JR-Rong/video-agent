from __future__ import annotations

import os
import requests
from typing import List

from .base import ExternalMediaCandidate


class PexelsClient:
    def __init__(self, *, api_key: str, timeout_sec: int = 30) -> None:
        if not api_key:
            raise RuntimeError("Missing PEXELS_API_KEY")
        self.api_key = api_key
        self.timeout = timeout_sec

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.api_key}

    def search_videos(self, *, query: str, per_page: int = 8, orientation: str = "portrait") -> list[ExternalMediaCandidate]:
        url = "https://api.pexels.com/videos/search"
        params = {"query": query, "per_page": int(per_page)}
        if orientation in ("portrait", "landscape", "square"):
            params["orientation"] = orientation

        r = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Pexels video search failed {r.status_code}: {r.text}")
        data = r.json()

        out: list[ExternalMediaCandidate] = []
        for v in data.get("videos", []):
            vid = str(v.get("id"))
            page_url = v.get("url") or ""
            duration = v.get("duration")
            user = v.get("user") or {}
            author = user.get("name")

            files = v.get("video_files") or []
            best = None
            for f in files:
                if f.get("file_type") == "video/mp4" and f.get("link"):
                    best = f
                    break
            if not best:
                continue

            out.append(
                ExternalMediaCandidate(
                    provider="pexels",
                    kind="video",
                    id=vid,
                    page_url=page_url,
                    download_url=best["link"],
                    width=int(best.get("width") or 0),
                    height=int(best.get("height") or 0),
                    duration=int(duration) if duration is not None else None,
                    author=author,
                    license_note="Pexels License (check terms).",
                )
            )
        return out

    def search_images(self, *, query: str, per_page: int = 8, orientation: str = "portrait") -> list[ExternalMediaCandidate]:
        url = "https://api.pexels.com/v1/search"
        params = {"query": query, "per_page": int(per_page)}
        if orientation in ("portrait", "landscape", "square"):
            params["orientation"] = orientation

        r = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Pexels image search failed {r.status_code}: {r.text}")
        data = r.json()

        out: list[ExternalMediaCandidate] = []
        for p in data.get("photos", []):
            pid = str(p.get("id"))
            page_url = p.get("url") or ""
            src = p.get("src") or {}
            # pick large2x or original
            dl = src.get("large2x") or src.get("original")
            if not dl:
                continue
            out.append(
                ExternalMediaCandidate(
                    provider="pexels",
                    kind="image",
                    id=pid,
                    page_url=page_url,
                    download_url=dl,
                    width=int(p.get("width") or 0),
                    height=int(p.get("height") or 0),
                    author=(p.get("photographer") or None),
                    license_note="Pexels License (check terms).",
                )
            )
        return out

    def download(self, *, url: str, out_path: str) -> str:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with requests.get(url, stream=True, timeout=self.timeout) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return out_path