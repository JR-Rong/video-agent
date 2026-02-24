from __future__ import annotations

import os
from typing import Any

from ...utils.files import ensure_dir, copy_file
from .base import UploadTask


class QueueCopyUploader:
    def __init__(self, *, upload_root: str, queue_subdir: str) -> None:
        self.dir = os.path.join(upload_root, queue_subdir)
        ensure_dir(self.dir)

    def upload(self, task: UploadTask) -> dict[str, Any]:
        dst = os.path.join(self.dir, os.path.basename(task.video_path))
        copy_file(task.video_path, dst)
        return {"type": "queue_copy", "queued_path": dst}