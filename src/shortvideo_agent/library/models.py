from __future__ import annotations

from pydantic import BaseModel
from typing import Literal


MediaType = Literal["image", "video"]
Orientation = Literal["portrait", "landscape"]


class MediaItem(BaseModel):
    id: int | None = None
    media_type: MediaType
    file_path: str

    keywords: list[str]
    prompt: str
    negative_prompt: str | None = None

    width: int | None = None
    height: int | None = None
    seconds: int | None = None
    orientation: Orientation | None = None  # portrait/landscape

    created_at: str
    usage_count: int = 0