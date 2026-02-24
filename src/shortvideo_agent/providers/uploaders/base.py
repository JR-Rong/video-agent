from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class UploadTask:
    platform: str
    title: str
    description: str
    video_path: str


class Uploader(Protocol):
    def upload(self, task: UploadTask) -> dict[str, Any]:
        """Return platform-specific result (e.g., video_id, publish_url, status)."""
        ...