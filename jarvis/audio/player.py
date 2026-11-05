"""Odtwarzanie na głośniku (JBL przez PipeWire/Bluetooth).

Strumień wyjściowy jest otwarty cały czas, a gdy nie ma czego grać, leci
cisza. Dzięki temu głośnik Bluetooth nie usypia i nie ucina początku
pierwszego słowa (to też jest część "przyspieszenia poza mózgiem").
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import wave
from pathlib import Path

from .dsp import BUILTIN_SOUNDS, Resampler, tone

log = logging.getLogger("jarvis.audio")


def load_sound(name: str, sounds_dir: Path, rate: int) -> bytes:
    path = sounds_dir / f"{name}.wav"
    if path.exists():
        with wave.open(str(path), "rb") as w:
            pcm = w.readframes(w.getnframes())
            if w.getnchannels() == 2:
                import numpy as np
                pcm = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 2).mean(axis=1).astype(np.int16).tobytes()
            return Resampler(w.getframerate(), rate).process(pcm)
    return tone(BUILTIN_SOUNDS.get(name, [(500, 0.1)]), rate)


class SoundDevicePlayer:
    def __init__(self, rate: int = 22050, device: str | int | None = None,
                 sounds_dir: Path | None = None, keepalive: bool = True, block_ms: int = 40):
        self.rate = rate
        self.device = device
        self.sounds_dir = sounds_dir or Path("sounds")
        self.keepalive = keepalive
        self.block = int(rate * block_ms / 1000)
        self._q: queue.Queue[bytes] = queue.Queue()
        self._pending = 0
        self._cond = threading.Condition()
        self._resamplers: dict[int, Resampler] = {}
        self._sounds: dict[str, bytes] = {}
        self._thread = threading.Thread(target=self._run, name="player", daemon=True)
        self._stop = threading.Event()

    def start(self) -> None:
        for name in BUILTIN_SOUNDS:
            self._sounds[name] = load_sound(name, self.sounds_dir, self.rate)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Kończy po dograniu kolejki; zamknięcie strumienia dogrywa też bufor karty."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    async def play(self, pcm: bytes, sample_rate: int) -> None:
        if sample_rate != self.rate:
            rs = self._resamplers.setdefault(sample_rate, Resampler(sample_rate, self.rate))
            pcm = rs.process(pcm)
        with self._cond:
            self._pending += 1
        self._q.put(pcm)

    async def drain(self) -> None:
        def wait() -> None:
            with self._cond:
                self._cond.wait_for(lambda: self._pending == 0)
        await asyncio.get_running_loop().run_in_executor(None, wait)

    async def play_sound(self, name: str) -> None:
        pcm = self._sounds.get(name) or load_sound(name, self.sounds_dir, self.rate)
        await self.play(pcm, self.rate)

    def _run(self) -> None:
        import sounddevice as sd

        silence = bytes(self.block * 2)
        with sd.RawOutputStream(samplerate=self.rate, channels=1, dtype="int16",
                                device=self.device, blocksize=self.block) as stream:
            while not (self._stop.is_set() and self._q.empty()):
                try:
                    pcm = self._q.get(timeout=0.02)
                except queue.Empty:
                    if self.keepalive:
                        stream.write(silence)
                    continue
                for i in range(0, len(pcm), self.block * 2):
                    stream.write(pcm[i:i + self.block * 2])
                with self._cond:
                    self._pending -= 1
                    self._cond.notify_all()
