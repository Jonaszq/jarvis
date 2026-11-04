"""Mikrofon, wake word i wykrywanie końca wypowiedzi.

Mikrofon "USB PnP Audio Device" nagrywa w swojej natywnej częstotliwości,
a my resamplujemy do 16 kHz mono. Jeden wątek nagrywa ciągle, a bloki
trafiają do kolejki asyncio. Ostatnia sekunda jest zawsze w buforze
(pre-roll), żeby nie ucinać początku polecenia zaraz po wake wordzie.

Etap 1: koniec wypowiedzi wykrywany progiem głośności (RMS).
Etap 4: Silero VAD (~450 ms ciszy).
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from ..orchestrator import Utterance
from .dsp import Resampler, rms

log = logging.getLogger("jarvis.audio")


class WakeDetector(Protocol):
    def score(self, block: bytes) -> float: ...
    def reset(self) -> None: ...


def wakeword_model_path(model: str) -> str:
    """Nazwa wbudowanego modelu (np. hey_jarvis) albo ścieżka do własnego .onnx."""
    if model.endswith((".onnx", ".tflite")):
        return str(Path(model).expanduser())
    import openwakeword  # type: ignore

    models = Path(openwakeword.__file__).parent / "resources" / "models"
    found = sorted(models.glob(f"{model}*.onnx"))
    if not found:
        raise FileNotFoundError(f"brak modelu wake worda {model!r} w {models}")
    return str(found[-1])


class OpenWakeWord:
    def __init__(self, model: str = "hey_jarvis"):
        from openwakeword.model import Model  # type: ignore

        path = wakeword_model_path(model)
        try:  # openwakeword 0.4 (ta wersja działa na Pi z Pythonem 3.13)
            self._model = Model(wakeword_model_paths=[path])
        except TypeError:  # 0.6+
            self._model = Model(wakeword_models=[path], inference_framework="onnx")
        self._name = model

    def score(self, block: bytes) -> float:
        scores = self._model.predict(np.frombuffer(block, dtype=np.int16))
        return float(max(scores.values())) if scores else 0.0

    def reset(self) -> None:
        self._model.reset()


@dataclass
class VadConfig:
    silence_ms: int = 700
    energy_threshold: float = 500.0   # minimalny próg; faktyczny rośnie z szumem tła
    max_record_s: float = 12.0
    no_speech_timeout_s: float = 4.0
    noise_factor: float = 1.8         # mowa = głośniej niż szum tła razy tyle
    max_threshold: float = 1200.0     # próg nigdy wyżej (inaczej cichszy głos ginie w szumie)
    hold_factor: float = 0.6          # po starcie mowy: dalej mowa, póki > próg x tyle


class NoiseFloor:
    """Bieżący poziom szumu tła (RMS), liczony w czasie czekania na wake word.

    Stały próg zawodzi, gdy szum się zmienia (np. syk z wyjścia audio Pi
    przez kabel): szum ciągle "brzmi jak mowa" i nagrywanie trwa do limitu.
    """

    def __init__(self, initial: float = 200.0, alpha: float = 0.02):
        self.level = initial
        self.alpha = alpha

    def update(self, level: float) -> None:
        # Głośne bloki (mowa) prawie nie podnoszą szumu; ciche obniżają szybko.
        if level < self.level:
            self.level += 0.2 * (level - self.level)
        elif level < self.level * 3:
            self.level += self.alpha * (level - self.level)

    def threshold(self, vad: "VadConfig") -> float:
        return max(vad.energy_threshold, min(vad.max_threshold, self.level * vad.noise_factor))


def find_input_device(name: str | None) -> tuple[int | None, int]:
    """Zwraca (indeks, natywna częstotliwość) urządzenia, którego nazwa zawiera `name`."""
    import sounddevice as sd

    if name:
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
                return idx, int(dev["default_samplerate"])
    else:
        default = sd.query_devices(kind="input")
        return None, int(default["default_samplerate"])
    default = sd.query_devices(kind="input")
    log.warning("nie znaleziono mikrofonu %r, biorę domyślny: %s", name, default["name"])
    return None, int(default["default_samplerate"])


class MicListener:
    def __init__(
        self,
        wake: WakeDetector,
        device: str | None = None,
        input_rate: int | None = None,
        rate: int = 16000,
        block_ms: int = 80,
        preroll_s: float = 1.0,
        wake_threshold: float = 0.5,
        vad: VadConfig | None = None,
        on_wake: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        streamer=None,  # np. ScribeRealtime: audio leci do STT już w trakcie mówienia
    ):
        self.wake = wake
        self.device_name = device
        self.input_rate = input_rate
        self.rate = rate
        self.block_bytes = int(rate * block_ms / 1000) * 2
        self.block_s = block_ms / 1000
        self.wake_threshold = wake_threshold
        self.vad = vad or VadConfig()
        self.on_wake = on_wake
        self.clock = clock
        self.streamer = streamer
        self.noise = NoiseFloor()
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=max(1, int(preroll_s / self.block_s)))
        self._q: asyncio.Queue[bytes] = asyncio.Queue()
        self._stream = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        import sounddevice as sd

        idx, native = find_input_device(self.device_name)
        src_rate = self.input_rate or native
        resampler = Resampler(src_rate, self.rate)
        pending = bytearray()
        log.info("mikrofon: urządzenie %s, %d Hz -> %d Hz", idx, src_rate, self.rate)

        def callback(indata, frames, time_info, status):  # wątek audio
            if status:
                log.debug("mikrofon: %s", status)
            pending.extend(resampler.process(bytes(indata)))
            while len(pending) >= self.block_bytes:
                block = bytes(pending[: self.block_bytes])
                del pending[: self.block_bytes]
                loop.call_soon_threadsafe(self._q.put_nowait, block)

        self._stream = sd.RawInputStream(
            samplerate=src_rate, channels=1, dtype="int16", device=idx,
            blocksize=int(src_rate * self.block_s), callback=callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()

    def flush(self) -> None:
        """Wyrzuca bloki nagrane w czasie, gdy Jarvis mówił (bez AEC słyszy sam siebie)."""
        while not self._q.empty():
            self._q.get_nowait()
        self._preroll.clear()

    async def _block(self) -> bytes:
        block = await self._q.get()
        self._preroll.append(block)
        return block

    async def next_utterance(self, require_wake: bool, timeout_s: float | None) -> Utterance | None:
        self.flush()
        wake_t = None
        if require_wake:
            self.wake.reset()
            while True:
                block = await self._block()
                self.noise.update(rms(block))
                if self.wake.score(block) >= self.wake_threshold:
                    wake_t = self.clock()
                    log.info("wake word")
                    if self.on_wake:
                        self.on_wake()
                    break
        return await self._record(wake_t, timeout_s if not require_wake else self.vad.no_speech_timeout_s)

    async def _record(self, wake_t: float | None, start_timeout_s: float | None) -> Utterance | None:
        frames: list[bytes] = list(self._preroll) if wake_t is not None else []
        silence_blocks = int(self.vad.silence_ms / 1000 / self.block_s)
        max_blocks = int(self.vad.max_record_s / self.block_s)
        start_blocks = int((start_timeout_s or self.vad.no_speech_timeout_s) / self.block_s)
        threshold = self.noise.threshold(self.vad)
        log.info("próg mowy %.0f (szum tła %.0f)", threshold, self.noise.level)
        # Strumieniowe STT: po wake wordzie od razu, w dopytaniu dopiero gdy ktoś mówi.
        session = self.streamer.session() if (self.streamer and wake_t is not None) else None
        if session:
            for f in frames:
                session.feed(f)
        heard = False
        quiet = 0
        n = 0
        try:
            while n < max_blocks:
                block = await self._block()
                frames.append(block)
                n += 1
                # Histereza: start mowy wymaga pełnego progu, ale ciche końcówki
                # słów i krótkie przerwy nie ucinają nagrania.
                loud = rms(block) >= (threshold * self.vad.hold_factor if heard else threshold)
                if session:
                    session.feed(block)
                elif loud and self.streamer:
                    session = self.streamer.session()
                    for f in frames[-4:]:
                        session.feed(f)
                if loud:
                    heard = True
                    quiet = 0
                elif heard:
                    quiet += 1
                    if quiet >= silence_blocks:
                        break
                elif n >= start_blocks:
                    break
        except BaseException:
            if session:
                session.abort()
            raise
        # Liczymy od faktycznego zamilknięcia, a nie od chwili, gdy VAD to zauważył,
        # więc czekanie na ciszę też wchodzi w zmierzone opóźnienie.
        speech_end_t = self.clock() - quiet * self.block_s
        if not heard:
            log.info("nikt nic nie powiedział")
            if session:
                session.abort()
            return None
        transcript = asyncio.ensure_future(session.finish()) if session else None
        # Ucinamy końcową ciszę poza ~200 ms, mniej audio = szybsze STT.
        trim = max(0, quiet - int(0.2 / self.block_s))
        if trim:
            frames = frames[:-trim]
        return Utterance(b"".join(frames), self.rate, wake_t, speech_end_t, transcript)
