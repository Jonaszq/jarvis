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
    energy_threshold: float = 500.0
    max_record_s: float = 12.0
    no_speech_timeout_s: float = 4.0


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
                if self.wake.score(await self._block()) >= self.wake_threshold:
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
        heard = False
        quiet = 0
        n = 0
        while n < max_blocks:
            block = await self._block()
            frames.append(block)
            n += 1
            loud = rms(block) >= self.vad.energy_threshold
            if loud:
                heard = True
                quiet = 0
            elif heard:
                quiet += 1
                if quiet >= silence_blocks:
                    break
            elif n >= start_blocks:
                log.info("nikt nic nie powiedział")
                return None
        # Liczymy od faktycznego zamilknięcia, a nie od chwili, gdy VAD to zauważył,
        # więc czekanie na ciszę też wchodzi w zmierzone opóźnienie.
        speech_end_t = self.clock() - quiet * self.block_s
        if not heard:
            return None
        # Ucinamy końcową ciszę poza ~200 ms, mniej audio = szybsze STT.
        trim = max(0, quiet - int(0.2 / self.block_s))
        if trim:
            frames = frames[:-trim]
        return Utterance(b"".join(frames), self.rate, wake_t, speech_end_t)
