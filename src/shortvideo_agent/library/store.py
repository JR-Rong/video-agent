from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import MediaItem, MediaType, Orientation
from ..utils.files import ensure_dir, copy_file


def _infer_orientation(width: int | None, height: int | None) -> Orientation | None:
    if not width or not height:
        return None
    return "portrait" if height >= width else "landscape"


class MediaLibrary:
    def __init__(self, root_dir: str) -> None:
        self.root_dir = root_dir
        self.db_path = os.path.join(root_dir, "library.sqlite3")
        self.images_dir = os.path.join(root_dir, "images")
        self.videos_dir = os.path.join(root_dir, "videos")
        ensure_dir(root_dir)
        ensure_dir(self.images_dir)
        ensure_dir(self.videos_dir)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_items (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  media_type TEXT NOT NULL,
                  file_path TEXT NOT NULL,
                  keywords_json TEXT NOT NULL,
                  prompt TEXT NOT NULL,
                  negative_prompt TEXT,
                  width INTEGER,
                  height INTEGER,
                  seconds INTEGER,
                  orientation TEXT,
                  created_at TEXT NOT NULL,
                  usage_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # 兼容旧库：若没有 orientation 列则尝试补列
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(media_items)").fetchall()]
            if "orientation" not in cols:
                conn.execute("ALTER TABLE media_items ADD COLUMN orientation TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_type ON media_items(media_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_orientation ON media_items(orientation)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage ON media_items(usage_count)")
            conn.commit()

    def add_item(
        self,
        *,
        media_type: MediaType,
        src_file_path: str,
        keywords: list[str],
        prompt: str,
        negative_prompt: str | None,
        width: int | None = None,
        height: int | None = None,
        seconds: int | None = None,
        orientation: Orientation | None = None,
    ) -> MediaItem:
        created_at = datetime.now(timezone.utc).isoformat()

        ext = Path(src_file_path).suffix.lower() or (".png" if media_type == "image" else ".mp4")
        base_dir = self.images_dir if media_type == "image" else self.videos_dir
        dst_file = os.path.join(base_dir, f"{created_at.replace(':','_')}_{os.path.basename(src_file_path)}")
        copy_file(src_file_path, dst_file)

        ori = orientation or _infer_orientation(width, height)

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO media_items(media_type, file_path, keywords_json, prompt, negative_prompt, width, height, seconds, orientation, created_at, usage_count)
                VALUES(?,?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    media_type,
                    dst_file,
                    json.dumps(keywords, ensure_ascii=False),
                    prompt,
                    negative_prompt,
                    width,
                    height,
                    seconds,
                    ori,
                    created_at,
                ),
            )
            conn.commit()
            item_id = int(cur.lastrowid)

        return MediaItem(
            id=item_id,
            media_type=media_type,
            file_path=dst_file,
            keywords=keywords,
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            seconds=seconds,
            orientation=ori,
            created_at=created_at,
            usage_count=0,
        )

    def increment_usage(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE media_items SET usage_count=usage_count+1 WHERE id=?", (item_id,))
            conn.commit()

    def list_items(self, *, media_type: MediaType, orientation: Orientation | None = None) -> list[MediaItem]:
        q = "SELECT * FROM media_items WHERE media_type=?"
        params: list = [media_type]
        if orientation:
            q += " AND (orientation=? OR orientation IS NULL)"
            params.append(orientation)
        q += " ORDER BY usage_count DESC, id DESC"

        with self._connect() as conn:
            rows = conn.execute(q, tuple(params)).fetchall()

        items: list[MediaItem] = []
        for r in rows:
            items.append(
                MediaItem(
                    id=r["id"],
                    media_type=r["media_type"],
                    file_path=r["file_path"],
                    keywords=json.loads(r["keywords_json"]),
                    prompt=r["prompt"],
                    negative_prompt=r["negative_prompt"],
                    width=r["width"],
                    height=r["height"],
                    seconds=r["seconds"],
                    orientation=r["orientation"],
                    created_at=r["created_at"],
                    usage_count=r["usage_count"],
                )
            )
        return items