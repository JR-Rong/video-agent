from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Storyboard:
    outline: dict[str, Any]
    script: dict[str, Any]


OUTLINE_SCHEMA_HINT = """{
  "title": "string",
  "hook": "string",
  "tone": "string",
  "setting": "string",
  "characters": ["string"],
  "plot_points": ["string"],
  "originality_check": {
    "why_original": "string",
    "avoid_references": ["string"]
  }
}"""

SCRIPT_SCHEMA_HINT = """{
  "series": "string",
  "episode": 1,
  "title": "string",
  "total_seconds": 30,
  "style": {
    "visual": "string",
    "music": "string"
  },
  "scenes": [
    {
      "id": 1,
      "seconds": 5,
      "media_type": "image",
      "narration": "string",
      "on_screen_text": "string",
      "image_prompt": "string",
      "video_prompt": "string",
      "negative_prompt": "string"
    }
  ],
  "tags": ["string"],
  "platform_title": "string",
  "platform_desc": "string"
}"""