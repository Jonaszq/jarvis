"""Rozpoznawanie mowy na bieżąco: ElevenLabs Scribe v2 Realtime (WebSocket).

Audio leci do ElevenLabs już w trakcie mówienia, więc po zamilknięciu
zostaje tylko "commit" i odbiór gotowego tekstu (zamiast wysyłania całego
nagrania i czekania). Połączenie otwieramy dopiero po wake wordzie (albo
przy pierwszym głośnym bloku w dopytaniu) i zamykamy po każdej wypowiedzi,
bo limit darmowego planu liczy się w godzinach strumienia.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from urllib.parse import urlencode

log = logging.getLogger("jarvis.stt.realtime")

URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"


async def _connect(url: str, headers: dict[str, str]):
    from websockets.asyncio.client import connect

    return await connect(url, additional_headers=headers, open_timeout=5, max_size=2**20)


class ScribeRealtime:
    def __init__(self, api_key: str, language: str | None = "pl", model: str = "scribe_v2_realtime",
                 sample_rate: int = 16000, url: str = URL):
        self.api_key = api_key
        self.sample_rate = sample_rate
        params = {"model_id": model, "audio_format": f"pcm_{sample_rate}", "commit_strategy": "manual"}
        if language:
            params["language_code"] = language
        self.url = f"{url}?{urlencode(params)}"

    def session(self) -> "ScribeSession":
        s = ScribeSession(self)
        s.begin()
        return s


class ScribeSession:
    def __init__(self, owner: ScribeRealtime):
        self.owner = owner
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._committed: list[str] = []
        self._done = asyncio.Event()
        self._error: BaseException | None = None
        self._task: asyncio.Task | None = None

    def begin(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    def feed(self, pcm: bytes) -> None:
        self._q.put_nowait(pcm)

    async def finish(self, timeout: float = 5.0) -> str:
        """Kończy wypowiedź i zwraca tekst (rzuca wyjątek, gdy się nie udało)."""
        self._q.put_nowait(None)
        try:
            await asyncio.wait_for(self._done.wait(), timeout)
        finally:
            if self._task and not self._task.done():
                self._task.cancel()
        if self._error:
            raise self._error
        return " ".join(" ".join(self._committed).split())

    def abort(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        try:
            ws = await _connect(self.owner.url, {"xi-api-key": self.owner.api_key})
        except BaseException as exc:
            self._fail(exc)
            return
        reader = asyncio.ensure_future(self._read(ws))
        try:
            while True:
                pcm = await self._q.get()
                if pcm is None:
                    # Commit z krótką ciszą: serwer domyka ostatnie słowo.
                    silence = bytes(int(self.owner.sample_rate * 0.1) * 2)
                    await ws.send(self._chunk(silence, commit=True))
                    break
                await ws.send(self._chunk(pcm))
            await reader
        except BaseException as exc:
            self._fail(exc)
        finally:
            reader.cancel()
            try:
                await ws.close()
            except Exception:
                pass

    async def _read(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            kind = msg.get("message_type")
            if kind == "committed_transcript":
                self._committed.append(msg.get("text") or "")
                self._done.set()
                return
            if kind in ("partial_transcript", "session_started", "warning"):
                if kind == "warning":
                    log.info("Scribe: %s", msg.get("warning"))
                continue
            if "error" in msg or "error" in str(kind):
                raise RuntimeError(f"Scribe realtime: {kind}: {msg.get('error') or msg}")
        raise RuntimeError("Scribe realtime: połączenie zamknięte bez tekstu")

    def _chunk(self, pcm: bytes, commit: bool = False) -> str:
        msg = {"message_type": "input_audio_chunk", "audio_base_64": base64.b64encode(pcm).decode()}
        if commit:
            msg["commit"] = True
        return json.dumps(msg)

    def _fail(self, exc: BaseException) -> None:
        if not isinstance(exc, asyncio.CancelledError):
            self._error = exc
            log.warning("Scribe realtime nie działa: %s", exc)
        self._done.set()
