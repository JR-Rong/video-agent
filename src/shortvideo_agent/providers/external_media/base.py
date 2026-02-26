from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

MediaKind = Literal["video", "image"]


@dataclass(frozen=True)
class ExternalMediaCandidate:
    provider: str
    kind: MediaKind
    id: str

    page_url: str
    download_url: str

    width: int | None = None
    height: int | None = None
    duration: int | None = None  # seconds, for video

    author: str | None = None
    license_note: str | None = None