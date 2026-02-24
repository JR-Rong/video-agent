from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


class GenerationRecord(BaseModel):
    id: int | None = None
    series: str
    category: str
    user_prompt: str
    created_at: str

    # main artifacts
    outline: dict[str, Any] = Field(default_factory=dict)
    script: dict[str, Any] = Field(default_factory=dict)
    assets: dict[str, Any] = Field(default_factory=dict)
    final_video_path: str | None = None