"""ElevenLabs: rozpoznawanie mowy (Scribe) i mowa (Flash v2.5).

Klucz API bierzemy tylko ze zmiennej ELEVENLABS_API_KEY (na Pi z pliku
/etc/jarvis/env). Jedno połączenie HTTP jest trzymane otwarte, żeby kolejne
zapytania nie płaciły za nowe połączenie TLS.

Etap teraz: TTS przez HTTP streaming (zdanie po zdaniu), STT jednym
zapytaniem po końcu wypowiedzi. WebSockety (auto_mode, Scribe Realtime)
dojdą w etapie 3-4.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from typing import AsyncIterator

import httpx

log = logging.getLogger("jarvis.elevenlabs")

API = "https://api.elevenlabs.io"


class ElevenLabsError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def quota(self) -> bool:
        """Skończył się limit (albo prawie): przełączamy na zapas na dłużej."""
        return self.code in ("quota_exceeded", "quota_soft_limit", "payment_required") or self.status == 402


def _error(resp: httpx.Response, body: bytes) -> ElevenLabsError:
    code = None
    message = body[:300].decode(errors="replace")
    try:
        detail = httpx.Response(200, content=body).json().get("detail")
        if isinstance(detail, dict):
            code = detail.get("status") or detail.get("code")
            message = detail.get("message") or message
        elif isinstance(detail, str):
            message = detail
    except Exception:
        pass
    return ElevenLabsError(f"ElevenLabs {resp.status_code}: {message}", resp.status_code, code)


class ElevenLabsClient:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None,
                 quota_switch: float = 0.95, usage_refresh_s: float = 600.0):
        self._client = httpx.AsyncClient(
            base_url=API,
            headers={"xi-api-key": api_key},
            timeout=httpx.Timeout(20.0, connect=5.0),
            transport=transport,
            http2=False,
        )
        self.quota_switch = quota_switch
        self.usage_refresh_s = usage_refresh_s
        self.usage: tuple[int, int] | None = None  # (zużyte, limit) kredytów
        self.reset_unix: int | None = None         # kiedy limit się odnawia
        self._usage_t = 0.0
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self.refresh_usage()

    async def close(self) -> None:
        await self._client.aclose()

    async def refresh_usage(self) -> None:
        """Stan limitu. Klucz bez uprawnienia user_read: po prostu nie wiemy."""
        self._usage_t = time.monotonic()
        try:
            r = await self._client.get("/v1/user/subscription")
            if r.status_code == 200:
                data = r.json()
                self.usage = (int(data.get("character_count", 0)), int(data.get("character_limit", 0)))
                self.reset_unix = data.get("next_character_count_reset_unix")
                log.info("ElevenLabs: zużyte %d z %d kredytów", *self.usage)
            else:
                log.info("ElevenLabs: nie mogę odczytać limitu (%s)", r.status_code)
        except httpx.HTTPError as exc:
            log.warning("ElevenLabs: brak połączenia przy sprawdzaniu limitu: %s", exc)

    def check_quota(self) -> None:
        if time.monotonic() - self._usage_t > self.usage_refresh_s:
            asyncio.ensure_future(self.refresh_usage())
        if self.usage and self.usage[1] and self.usage[0] >= self.quota_switch * self.usage[1]:
            raise ElevenLabsError(
                f"ElevenLabs: zużyte {self.usage[0]}/{self.usage[1]} kredytów (próg {self.quota_switch:.0%})",
                code="quota_soft_limit",
            )

    def note_spent(self, credits: int) -> None:
        if self.usage:
            self.usage = (self.usage[0] + credits, self.usage[1])

    @property
    def http(self) -> httpx.AsyncClient:
        return self._client


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class ElevenLabsTTS:
    def __init__(self, client: ElevenLabsClient, voice_id: str, model: str = "eleven_flash_v2_5",
                 language: str | None = "pl", sample_rate: int = 22050,
                 voice_settings: dict | None = None):
        self.client = client
        self.voice_id = voice_id
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self.voice_settings = voice_settings

    async def start(self) -> None:
        await self.client.start()

    async def close(self) -> None:
        pass

    async def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        self.client.check_quota()
        body: dict = {"text": sentence, "model_id": self.model}
        if self.language:
            body["language_code"] = self.language
        if self.voice_settings:
            body["voice_settings"] = self.voice_settings
        url = f"/v1/text-to-speech/{self.voice_id}/stream"
        params = {"output_format": f"pcm_{self.sample_rate}"}
        async with self.client.http.stream("POST", url, params=params, json=body) as resp:
            if resp.status_code >= 400:
                raise _error(resp, await resp.aread())
            carry = b""
            async for chunk in resp.aiter_bytes():
                data = carry + chunk
                cut = len(data) - (len(data) % 2)  # pełne próbki 16-bit
                carry = data[cut:]
                if cut:
                    yield data[:cut]
        self.client.note_spent(len(sentence))


class ElevenLabsSTT:
    def __init__(self, client: ElevenLabsClient, model: str = "scribe_v1", language: str | None = "pl"):
        self.client = client
        self.model = model
        self.language = language

    async def start(self) -> None:
        await self.client.start()

    async def transcribe(self, audio: bytes, sample_rate: int) -> str:
        data = {"model_id": self.model, "tag_audio_events": "false"}
        if self.language:
            data["language_code"] = self.language
        files = {"file": ("mowa.wav", pcm_to_wav(audio, sample_rate), "audio/wav")}
        resp = await self.client.http.post("/v1/speech-to-text", data=data, files=files)
        if resp.status_code >= 400:
            raise _error(resp, resp.content)
        return " ".join(str(resp.json().get("text", "")).split())
