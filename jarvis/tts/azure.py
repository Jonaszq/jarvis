"""Głos z Microsoft Azure Speech (np. pl-PL-MarekNeural), plan darmowy F0.

Klucz i region tylko w /etc/jarvis/env:
    AZURE_SPEECH_KEY=...
    AZURE_SPEECH_REGION=westeurope
REST: jedno zdanie = jedno zapytanie, audio surowe PCM 24 kHz, strumieniowo.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator
from xml.sax.saxutils import escape

import httpx

log = logging.getLogger("jarvis.tts.azure")


class AzureError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def quota(self) -> bool:  # 429 = limit planu darmowego (na minutę albo miesiąc)
        return self.status in (403, 429)


class AzureTTS:
    sample_rate = 24000

    def __init__(self, key: str, region: str, voice: str = "pl-PL-MarekNeural", rate: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.key = key
        self.region = region
        self.voice = voice
        self.rate = rate  # np. "+10%"
        self.url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), transport=transport,
                                         http2=False)
        self.chars = 0

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        await self._client.aclose()

    def ssml(self, sentence: str) -> str:
        text = escape(sentence)
        if self.rate:
            text = f'<prosody rate="{self.rate}">{text}</prosody>'
        lang = "-".join(self.voice.split("-")[:2])
        return (f'<speak version="1.0" xml:lang="{lang}"><voice name="{self.voice}">{text}</voice></speak>')

    async def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "raw-24khz-16bit-mono-pcm",
            "User-Agent": "jarvis",
        }
        async with self._client.stream("POST", self.url, headers=headers,
                                       content=self.ssml(sentence).encode("utf-8")) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread())[:200]
                raise AzureError(f"Azure TTS HTTP {resp.status_code}: {body!r}", resp.status_code)
            carry = b""
            async for chunk in resp.aiter_bytes():
                data = carry + chunk
                cut = len(data) - (len(data) % 2)
                carry = data[cut:]
                if cut:
                    yield data[:cut]
        self.chars += len(sentence)
