from __future__ import annotations

import asyncio
from pathlib import Path
import edge_tts


async def _tts_async(text: str, out_path: str, voice: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(out_path)
    return out_path


def tts_to_mp3(*, text: str, out_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str:
    return asyncio.run(_tts_async(text, out_path, voice))