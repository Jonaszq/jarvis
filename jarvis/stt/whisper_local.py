"""STT lokalnie przez whisper.cpp (zapas, gdy ElevenLabs niedostępny).

Etap 1: wywołanie `whisper-cli` na pliku wav. Model wielojęzyczny
(tiny/base, nie .en), język polski. W etapie 2 przejdziemy na
`whisper-server`, który trzyma model w pamięci.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import wave
from pathlib import Path

log = logging.getLogger("jarvis.stt")


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


class WhisperLocalSTT:
    def __init__(self, cli: Path, model: Path, language: str = "pl", threads: int = 4):
        self.cli = Path(cli).expanduser()
        self.model = Path(model).expanduser()
        self.language = language
        self.threads = threads

    async def transcribe(self, audio: bytes, sample_rate: int) -> str:
        with tempfile.TemporaryDirectory(prefix="jarvis-stt-") as tmp:
            wav = Path(tmp) / "in.wav"
            write_wav(wav, audio, sample_rate)
            proc = await asyncio.create_subprocess_exec(
                str(self.cli), "-m", str(self.model), "-f", str(wav),
                "-l", self.language, "-t", str(self.threads), "-nt", "-np",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli kod {proc.returncode}: {err.decode(errors='replace')[-300:]}")
        text = " ".join(out.decode("utf-8", errors="replace").split())
        # Whisper w ciszy potrafi zwrócić "[BLANK_AUDIO]" albo "(muzyka)".
        if text.startswith(("[", "(")) and text.endswith(("]", ")")):
            return ""
        return text
