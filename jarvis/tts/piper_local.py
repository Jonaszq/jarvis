"""TTS lokalnie przez Pipera, z głosem ładowanym raz na starcie.

Dotychczasowe ~23 s to głównie ładowanie modelu przy każdym zdaniu. Tutaj
model ładuje się raz:
- tryb "python": pakiet piper-tts w tym samym procesie (preferowany),
- tryb "cli": jeden długo żyjący proces `piper --output_dir`, który dla
  każdej linii tekstu zapisuje plik wav i wypisuje jego ścieżkę.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import wave
from pathlib import Path
from typing import AsyncIterator

log = logging.getLogger("jarvis.tts")


def voice_sample_rate(voice: Path) -> int:
    meta = Path(str(voice) + ".json")
    try:
        return int(json.loads(meta.read_text())["audio"]["sample_rate"])
    except (OSError, KeyError, ValueError):
        return 22050


class PiperTTS:
    def __init__(self, voice: Path, piper_bin: str = "piper", mode: str = "auto",
                 sample_rate: int | None = None):
        self.voice = Path(voice).expanduser()
        self.piper_bin = piper_bin
        self.mode = mode
        self.sample_rate = sample_rate or voice_sample_rate(self.voice)
        self._voice_obj = None
        self._proc: asyncio.subprocess.Process | None = None
        self._outdir: tempfile.TemporaryDirectory | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.mode in ("auto", "python"):
            try:
                from piper import PiperVoice  # type: ignore
            except ImportError:
                if self.mode == "python":
                    raise
            else:
                loop = asyncio.get_running_loop()
                self._voice_obj = await loop.run_in_executor(None, PiperVoice.load, str(self.voice))
                self.mode = "python"
                log.info("Piper: głos załadowany w procesie (%s)", self.voice.name)
                return
        self.mode = "cli"
        await self._spawn()

    async def _spawn(self) -> None:
        self._outdir = tempfile.TemporaryDirectory(prefix="jarvis-piper-")
        self._proc = await asyncio.create_subprocess_exec(
            self.piper_bin, "--model", str(self.voice), "--output_dir", self._outdir.name,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        log.info("Piper: proces CLI uruchomiony (%s)", self.voice.name)

    async def close(self) -> None:
        if self._proc and self._proc.returncode is None:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), 2)
            except asyncio.TimeoutError:
                self._proc.kill()
        if self._outdir:
            self._outdir.cleanup()

    async def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        sentence = " ".join(sentence.split())
        if not sentence:
            return
        async with self._lock:
            if self.mode == "python":
                pcm = await asyncio.get_running_loop().run_in_executor(None, self._synth_python, sentence)
                yield pcm
            else:
                yield await self._synth_cli(sentence)

    def _synth_python(self, sentence: str) -> bytes:
        voice = self._voice_obj
        if hasattr(voice, "synthesize_stream_raw"):  # piper-tts 1.2
            return b"".join(voice.synthesize_stream_raw(sentence))
        chunks = list(voice.synthesize(sentence))   # piper-tts >= 1.3
        return b"".join(c.audio_int16_bytes for c in chunks)

    async def _synth_cli(self, sentence: str) -> bytes:
        if self._proc is None or self._proc.returncode is not None:
            await self._spawn()
        assert self._proc and self._proc.stdin and self._proc.stdout
        self._proc.stdin.write((sentence + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        line = await asyncio.wait_for(self._proc.stdout.readline(), 60)
        if not line:
            raise RuntimeError("proces Pipera się zakończył")
        path = Path(line.decode().strip())
        with wave.open(str(path), "rb") as w:
            self.sample_rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
        path.unlink(missing_ok=True)
        return pcm
