"""Automatyczny zapas: chmura (ElevenLabs) z przełączaniem na lokalne.

Gdy chmura zawiedzie (brak internetu, błąd, koniec limitu), to samo zdanie
albo nagranie idzie do zapasu (Piper/Whisper), a chmura dostaje przerwę:
krótką po zwykłym błędzie, długą po końcu limitu. Po przerwie próbujemy
znowu sami, bez restartu.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Callable

log = logging.getLogger("jarvis.fallback")


class _Breaker:
    def __init__(self, name: str, error_cooldown_s: float, quota_cooldown_s: float,
                 clock: Callable[[], float]):
        self.name = name
        self.error_cooldown_s = error_cooldown_s
        self.quota_cooldown_s = quota_cooldown_s
        self.clock = clock
        self.until = 0.0

    def open(self) -> bool:
        return self.clock() >= self.until

    def trip(self, exc: BaseException) -> None:
        quota = bool(getattr(exc, "quota", False))
        pause = self.quota_cooldown_s if quota else self.error_cooldown_s
        self.until = self.clock() + pause
        log.warning("%s: przechodzę na zapas na %.0f s (%s)", self.name, pause, exc)


class _LazyStart:
    """Zapas startuje w tle (Piper ładuje się ~20 s), żeby nie blokować startu."""

    def __init__(self, backup):
        self.backup = backup
        self._task: asyncio.Task | None = None

    def kick(self) -> None:
        if self._task is None and hasattr(self.backup, "start"):
            self._task = asyncio.ensure_future(self.backup.start())

    async def ready(self):
        self.kick()
        if self._task is not None:
            await self._task
        return self.backup


class FallbackTTS:
    def __init__(self, primary, backup, error_cooldown_s: float = 60.0,
                 quota_cooldown_s: float = 6 * 3600.0, clock: Callable[[], float] = time.monotonic):
        self.primary = primary
        self.backup = _LazyStart(backup)
        self.breaker = _Breaker("TTS", error_cooldown_s, quota_cooldown_s, clock)
        self.sample_rate = primary.sample_rate
        self.last_engine = "primary"

    async def start(self) -> None:
        self.backup.kick()
        try:
            await self.primary.start()
        except Exception as exc:
            self.breaker.trip(exc)

    async def close(self) -> None:
        backup = await self.backup.ready()
        await asyncio.gather(self.primary.close(), backup.close(), return_exceptions=True)

    async def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        if self.breaker.open():
            got_audio = False
            try:
                self.sample_rate = self.primary.sample_rate
                async for pcm in self.primary.synthesize(sentence):
                    got_audio = True
                    self.last_engine = "primary"
                    yield pcm
                return
            except Exception as exc:
                self.breaker.trip(exc)
                if got_audio:
                    return  # zdanie urwane w połowie, następne pójdzie już z zapasu
        backup = await self.backup.ready()
        self.last_engine = "backup"
        async for pcm in backup.synthesize(sentence):
            self.sample_rate = backup.sample_rate
            yield pcm


class FallbackSTT:
    def __init__(self, primary, backup, error_cooldown_s: float = 60.0,
                 quota_cooldown_s: float = 6 * 3600.0, clock: Callable[[], float] = time.monotonic):
        self.primary = primary
        self.backup = backup
        self.breaker = _Breaker("STT", error_cooldown_s, quota_cooldown_s, clock)
        self.last_engine = "primary"

    async def transcribe(self, audio: bytes, sample_rate: int) -> str:
        if self.breaker.open():
            try:
                text = await self.primary.transcribe(audio, sample_rate)
                self.last_engine = "primary"
                return text
            except Exception as exc:
                self.breaker.trip(exc)
        self.last_engine = "backup"
        return await self.backup.transcribe(audio, sample_rate)
