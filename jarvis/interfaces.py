"""Wspólne interfejsy wymiennych kroków: STT, mózg i TTS.

Orkiestrator zna tylko te protokoły, więc lokalny Whisper i ElevenLabs
(albo Piper i ElevenLabs) podmienia się w configu bez zmian w reszcie kodu.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol


class STT(Protocol):
    async def transcribe(self, audio: bytes, sample_rate: int) -> str:
        """Zamienia nagranie (16-bit PCM mono) na tekst."""
        ...


class Brain(Protocol):
    def ask(self, text: str) -> AsyncIterator[str]:
        """Zwraca odpowiedź kawałek po kawałku (tokeny albo całość naraz)."""
        ...

    async def start(self) -> None:
        """Przygotowuje mózg (np. uruchamia proces i robi rozgrzewkę)."""
        ...

    async def reset(self) -> None:
        """Zaczyna nową rozmowę bez historii."""
        ...

    async def close(self) -> None:
        ...


class TTS(Protocol):
    sample_rate: int

    def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        """Zwraca audio 16-bit PCM mono dla jednego zdania, kawałkami."""
        ...

    async def start(self) -> None:
        ...

    async def close(self) -> None:
        ...


class Player(Protocol):
    async def play(self, pcm: bytes, sample_rate: int) -> None:
        """Odtwarza kawałek PCM i wraca, gdy jest w kolejce do zagrania."""
        ...

    async def drain(self) -> None:
        """Czeka, aż wszystko z kolejki zostanie zagrane."""
        ...

    async def play_sound(self, name: str) -> None:
        """Odtwarza krótki dźwięk z katalogu sounds/ (ding, błąd itd.)."""
        ...
