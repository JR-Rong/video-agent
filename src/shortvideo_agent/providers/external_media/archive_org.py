from __future__ import annotations

import os
import requests
from urllib.parse import quote

from .base import ExternalMediaCandidate


class ArchiveOrgClient:
    """
    Basic Archive.org search:
    - advancedsearch.php to get identifiers
    - metadata/{identifier} to get files, pick mp4
    """
    def __init__(self, *, timeout_sec: int = 30) -> None:
        self.timeout = timeout_sec

    def search_videos(self, *, query: str, per_page: int = 5) -> list[ExternalMediaCandidate]:
        # advanced search
        # docs: https://archive.org/advancedsearch.php
        q = f'({query}) AND mediatype:(movies)'
        url = "https://archive.org/advancedsearch.php"
        params = {
            "q": q,
            "fl[]": "identifier",
            "rows": int(per_page),
            "page": 1,
            "output": "json",
        }
        r = requests.get(url, params=params, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Archive search failed {r.status_code}: {r.text}")
        data = r.json()
        docs = (((data.get("response") or {}).get("docs")) or [])

        out: list[ExternalMediaCandidate] = []
        for d in docs:
            ident = d.get("identifier")
            if not ident:
                continue

            meta_url = f"https://archive.org/metadata/{quote(str(ident))}"
            m = requests.get(meta_url, timeout=self.timeout)
            if m.status_code >= 400:
                continue
            meta = m.json()
            files = meta.get("files") or []

            mp4 = None
            for f in files:
                name = f.get("name") or ""
                if name.lower().endswith(".mp4"):
                    mp4 = name
                    break
            if not mp4:
                continue

            download_url = f"https://archive.org/download/{ident}/{quote(mp4)}"
            page_url = f"https://archive.org/details/{ident}"

            out.append(
                ExternalMediaCandidate(
                    provider="archive",
                    kind="video",
                    id=str(ident),
                    page_url=page_url,
                    download_url=download_url,
                    width=None,
                    height=None,
                    duration=None,
                    author=None,
                    license_note="Archive.org item license varies; verify per item.",
                )
            )
        return out