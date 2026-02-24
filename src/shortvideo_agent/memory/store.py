from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import GenerationRecord


class MemoryStore:
    def __init__(self, sqlite_path: str) -> None:
        self.sqlite_path = sqlite_path
        os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS generations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  series TEXT NOT NULL,
                  category TEXT NOT NULL,
                  user_prompt TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  outline_json TEXT NOT NULL,
                  script_json TEXT NOT NULL,
                  assets_json TEXT NOT NULL,
                  final_video_path TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_series ON generations(series)")
            conn.commit()

    def add_record(
        self,
        *,
        series: str,
        category: str,
        user_prompt: str,
        outline: dict[str, Any],
        script: dict[str, Any],
        assets: dict[str, Any],
        final_video_path: str | None,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO generations(series, category, user_prompt, created_at, outline_json, script_json, assets_json, final_video_path)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    series,
                    category,
                    user_prompt,
                    created_at,
                    json.dumps(outline, ensure_ascii=False),
                    json.dumps(script, ensure_ascii=False),
                    json.dumps(assets, ensure_ascii=False),
                    final_video_path,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_latest_by_series(self, series: str) -> GenerationRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM generations WHERE series=? ORDER BY id DESC LIMIT 1", (series,)
            ).fetchone()
            if not row:
                return None
            return GenerationRecord(
                id=row["id"],
                series=row["series"],
                category=row["category"],
                user_prompt=row["user_prompt"],
                created_at=row["created_at"],
                outline=json.loads(row["outline_json"]),
                script=json.loads(row["script_json"]),
                assets=json.loads(row["assets_json"]),
                final_video_path=row["final_video_path"],
            )

    def list_series(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT series FROM generations ORDER BY series").fetchall()
            return [r["series"] for r in rows]