from __future__ import annotations

import os
import requests

from .base import ExternalMediaCandidate


class PixabayClient:
    def __init__(self, *, api_key: str, timeout_sec: int = 30) -> None:
        if not api_key:
            raise RuntimeError("Missing PIXABAY_API_KEY")
        self.api_key = api_key
        self.timeout = timeout_sec

    def search_videos(self, *, query: str, per_page: int = 8) -> list[ExternalMediaCandidate]:
        url = "https://pixabay.com/api/videos/"
        params = {"key": self.api_key, "q": query, "per_page": int(per_page)}
        r = requests.get(url, params=params, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Pixabay video search failed {r.status_code}: {r.text}")
        data = r.json()

        out: list[ExternalMediaCandidate] = []
        for hit in data.get("hits", []):
            vid = str(hit.get("id"))
            page_url = hit.get("pageURL") or ""
            duration = hit.get("duration")
            user = hit.get("user")

            videos = hit.get("videos") or {}
            # choose medium or small mp4
            cand = videos.get("medium") or videos.get("small") or videos.get("tiny")
            if not cand:
                continue
            dl = cand.get("url")
            if not dl:
                continue

            out.append(
                ExternalMediaCandidate(
                    provider="pixabay",
                    kind="video",
                    id=vid,
                    page_url=page_url,
                    download_url=dl,
                    width=int(cand.get("width") or 0),
                    height=int(cand.get("height") or 0),
                    duration=int(duration) if duration is not None else None,
                    author=user,
                    license_note="Pixabay License (check terms).",
                )
            )
        return out

    def search_images(self, *, query: str, per_page: int = 8, orientation: str = "all") -> list[ExternalMediaCandidate]:
        url = "https://pixabay.com/api/"
        params = {
            "key": self.api_key,
            "q": query,
            "per_page": int(per_page),
            "image_type": "photo",
        }
        # pixabay orientation: "horizontal"|"vertical"|"all"
        if orientation in ("horizontal", "vertical", "all"):
            params["orientation"] = orientation

        r = requests.get(url, params=params, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Pixabay image search failed {r.status_code}: {r.text}")
        data = r.json()

        out: list[ExternalMediaCandidate] = []
        for hit in data.get("hits", []):
            pid = str(hit.get("id"))
            page_url = hit.get("pageURL") or ""
            dl = hit.get("largeImageURL") or hit.get("webformatURL")
            if not dl:
                continue
            out.append(
                ExternalMediaCandidate(
                    provider="pixabay",
                    kind="image",
                    id=pid,
                    page_url=page_url,
                    download_url=dl,
                    width=int(hit.get("imageWidth") or 0),
                    height=int(hit.get("imageHeight") or 0),
                    author=hit.get("user"),
                    license_note="Pixabay License (check terms).",
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