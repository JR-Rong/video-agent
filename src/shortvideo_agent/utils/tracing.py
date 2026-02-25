from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional
import logging

log = logging.getLogger(__name__)


@dataclass
class Tracer:
    trace_path: str

    def __post_init__(self) -> None:
        os.makedirs(os.path.dirname(self.trace_path), exist_ok=True)

    def emit(self, event: str, **fields: Any) -> None:
        rec = {
            "ts": time.time(),
            "event": event,
            **fields,
        }
        # console (compact)
        msg = f"[TRACE] {event} " + " ".join([f"{k}={fields[k]}" for k in sorted(fields.keys()) if k not in ("error", "payload")])
        if "error" in fields and fields["error"]:
            msg += f" error={fields['error']}"
        log.info(msg)

        # jsonl
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")