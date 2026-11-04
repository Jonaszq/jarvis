"""Główna pętla Jarvisa: czuwanie, nagrywanie, myślenie, mówienie, dopytanie.

Orkiestrator nie wie, czy STT/TTS są lokalne, czy w chmurze. Dostaje
adaptery zgodne z jarvis.interfaces i liczy czasy każdej tury.

Przepływ jednej tury:
  wypowiedź -> STT -> mózg (strumień tekstu) -> zdania -> TTS -> głośnik
Mózg, TTS i głośnik działają równolegle: pierwsze zdanie jest już
mówione, kiedy mózg jeszcze pisze dalsze.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from . import commands
from .interfaces import STT, TTS, Brain, Player
from .metrics import MetricsLog, Turn
from .text.sentences import SentenceSplitter

log = logging.getLogger("jarvis")


@dataclass
class Utterance:
    audio: bytes          # 16-bit PCM mono
    sample_rate: int
    wake_t: float | None  # czas wake worda (None przy dopytaniu)
    speech_end_t: float   # czas końca wypowiedzi (monotonic)


class Listener(Protocol):
    async def next_utterance(self, require_wake: bool, timeout_s: float | None) -> Utterance | None:
        """Czeka na wypowiedź. Z require_wake=True najpierw na wake word.

        Zwraca None, jeśli w timeout_s nikt nic nie powiedział.
        """
        ...


class Orchestrator:
    def __init__(
        self,
        listener: Listener,
        stt: STT,
        brain: Brain,
        tts: TTS,
        player: Player,
        metrics: MetricsLog | None = None,
        followup_s: float = 5.0,
        reset_after_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.listener = listener
        self.stt = stt
        self.brain = brain
        self.tts = tts
        self.player = player
        self.metrics = metrics
        self.followup_s = followup_s
        self.reset_after_s = reset_after_s
        self.clock = clock
        self.state = "czuwanie"
        self.sleeping = False          # po "wyłącz się": czeka tylko na "obudź się"
        self._end_conversation = False
        self._last_turn_t: float | None = None

    async def start(self) -> None:
        # Rozgrzewka na starcie, żeby pierwsze pytanie nie płaciło za zimny start.
        await asyncio.gather(self.brain.start(), self.tts.start())

    async def close(self) -> None:
        await asyncio.gather(self.brain.close(), self.tts.close(), return_exceptions=True)

    async def run(self) -> None:
        await self.start()
        try:
            while True:
                await self.run_once()
        finally:
            await self.close()

    async def run_once(self) -> None:
        """Jedna rozmowa: wake word, pytanie, a potem dopytania bez wake worda."""
        self.state = "uśpiony" if self.sleeping else "czuwanie"
        utterance = await self.listener.next_utterance(require_wake=True, timeout_s=None)
        while utterance is not None:
            self._end_conversation = False
            await self.handle(utterance)
            if self._end_conversation or self.sleeping:
                return
            self.state = "dopytanie"
            utterance = await self.listener.next_utterance(
                require_wake=False, timeout_s=self.followup_s
            )

    async def handle(self, utterance: Utterance) -> Turn:
        turn = Turn(clock=self.clock)
        if utterance.wake_t is not None:
            turn.marks["wake"] = utterance.wake_t
        turn.marks["speech_end"] = utterance.speech_end_t
        turn.info["followup"] = utterance.wake_t is None
        try:
            await self._turn(utterance, turn)
        except Exception as exc:  # jedna zła tura nie może położyć asystenta
            log.exception("tura nieudana")
            turn.info["error"] = f"{type(exc).__name__}: {exc}"
            try:
                await self.player.play_sound("blad")
            except Exception:
                log.exception("nie udało się zagrać dźwięku błędu")
        finally:
            turn.mark("done")
            for part, obj in (("stt", self.stt), ("tts", self.tts)):
                engine = getattr(obj, "last_engine", None)
                if engine:
                    turn.info[f"{part}_engine"] = engine
            self._last_turn_t = self.clock()
            if self.metrics:
                record = self.metrics.write(turn)
                log.info("tura: %s", record)
        return turn

    async def _turn(self, utterance: Utterance, turn: Turn) -> None:
        self.state = "nagrywanie"
        text = (await self.stt.transcribe(utterance.audio, utterance.sample_rate)).strip()
        turn.mark("transcript")
        turn.info["heard"] = text
        log.info("usłyszałem: %r", text)
        command = commands.match(text)
        if self.sleeping:
            if command != commands.WAKE:
                turn.info["ignored_sleeping"] = True  # śpi: bez odpowiedzi i bez kosztów
                self._end_conversation = True
                return
        if command:
            turn.info["command"] = command
            await self._command(command, turn)
            return
        if not text:
            turn.info["empty"] = True
            await self.player.play_sound("nie_slysze")
            return

        if self._last_turn_t is not None and self.clock() - self._last_turn_t > self.reset_after_s:
            await self.brain.reset()
            turn.info["reset"] = True

        self.state = "myślenie"
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak(sentences, turn))
        answer: list[str] = []
        try:
            splitter = SentenceSplitter()
            async for chunk in self.brain.ask(text):
                turn.mark("first_token")
                answer.append(chunk)
                for sentence in splitter.feed(chunk):
                    turn.mark("first_sentence")
                    await sentences.put(sentence)
            for sentence in splitter.flush():
                turn.mark("first_sentence")
                await sentences.put(sentence)
            turn.mark("brain_done")
        finally:
            await sentences.put(None)
            turn.info["answer"] = "".join(answer).strip()
            await speaker

    async def _command(self, command: str, turn: Turn) -> None:
        if command == commands.END:
            self._end_conversation = True
            await self._say("Jasne.", turn)
        elif command == commands.SLEEP:
            self.sleeping = True
            self._end_conversation = True
            log.info("uśpiony")
            await self._say("Dobra, wyłączam się. Obudzisz mnie słowami: hey Jarvis, obudź się.", turn)
        elif command == commands.WAKE:
            was_sleeping, self.sleeping = self.sleeping, False
            log.info("obudzony")
            await self._say("Jestem." if was_sleeping else "Przecież nie śpię.", turn)

    async def _say(self, text: str, turn: Turn) -> None:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        await queue.put(text)
        await queue.put(None)
        await self._speak(queue, turn)

    async def _speak(self, sentences: asyncio.Queue[str | None], turn: Turn) -> None:
        while (sentence := await sentences.get()) is not None:
            self.state = "mówienie"
            async for pcm in self.tts.synthesize(sentence):
                turn.mark("first_audio")
                await self.player.play(pcm, self.tts.sample_rate)
        await self.player.drain()
