"""Głos Marek (pl-PL-MarekNeural) przez edge-tts: usługę czytania na głos z
przeglądarki Edge. Za darmo, bez konta i klucza, ale nieoficjalnie: gdy
Microsoft to zablokuje, FallbackTTS przejdzie na Pipera.

edge-tts oddaje MP3, więc każde zdanie dekodujemy ffmpegiem do PCM 24 kHz.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

log = logging.getLogger("jarvis.tts.edge")


class EdgeError(RuntimeError):
    quota = False


async def mp3_to_pcm(mp3: bytes, rate: int = 24000, ffmpeg: str = "ffmpeg") -> bytes:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0",
        "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(mp3)
    if proc.returncode != 0:
        raise EdgeError(f"ffmpeg: {err.decode(errors='replace')[:200]}")
    return out


class EdgeTTS:
    sample_rate = 24000

    def __init__(self, voice: str = "pl-PL-MarekNeural", rate: str = "+0%", timeout_s: float = 10.0):
        self.voice = voice
        self.rate = rate
        self.timeout_s = timeout_s

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def _mp3(self, sentence: str) -> bytes:
        import edge_tts  # type: ignore

        comm = edge_tts.Communicate(sentence, self.voice, rate=self.rate)
        data = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                data += chunk["data"]
        return bytes(data)

    async def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        try:
            mp3 = await asyncio.wait_for(self._mp3(sentence), self.timeout_s)
        except asyncio.TimeoutError as exc:
            raise EdgeError("edge-tts: przekroczony czas") from exc
        except EdgeError:
            raise
        except Exception as exc:  # zmiany po stronie Microsoftu, brak sieci itd.
            raise EdgeError(f"edge-tts: {type(exc).__name__}: {exc}") from exc
        if not mp3:
            raise EdgeError("edge-tts: puste audio")
        pcm = await mp3_to_pcm(mp3, self.sample_rate)
        step = self.sample_rate * 2 // 5  # kawałki po 200 ms
        for i in range(0, len(pcm), step):
            yield pcm[i:i + step]
