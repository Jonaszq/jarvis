"""Proste przetwarzanie dźwięku na numpy: resampling, głośność, sygnały."""

from __future__ import annotations

import numpy as np


class Resampler:
    """Strumieniowy resampler int16 mono (filtr dolnoprzepustowy + interpolacja).

    Wystarcza do mowy: mikrofon 44,1/48 kHz -> 16 kHz i TTS 22,05 kHz -> wyjście.
    Pamięta stan między blokami, więc nie ma trzasków na granicach.
    """

    def __init__(self, src_rate: int, dst_rate: int, taps: int = 63):
        self.src = int(src_rate)
        self.dst = int(dst_rate)
        self.step = self.src / self.dst
        self._pos = 0.0                       # pozycja następnej próbki wyjścia (w próbkach wejścia)
        self._hist = np.zeros(0, dtype=np.float32)
        if self.dst < self.src:
            cutoff = 0.45 * self.dst / self.src  # trochę poniżej Nyquista celu
            n = np.arange(taps) - (taps - 1) / 2
            h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
            self._fir = (h / h.sum()).astype(np.float32)
        else:
            self._fir = None
        self._fir_tail = np.zeros(0 if self._fir is None else taps - 1, dtype=np.float32)

    def process(self, pcm: bytes) -> bytes:
        if self.src == self.dst:
            return pcm
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if self._fir is not None:
            buf = np.concatenate([self._fir_tail, x])
            y = np.convolve(buf, self._fir, mode="valid")
            self._fir_tail = buf[len(buf) - (len(self._fir) - 1):]
            x = y
        data = np.concatenate([self._hist, x])
        if len(data) < 2:
            self._hist = data
            return b""
        last = len(data) - 1
        positions = np.arange(self._pos, last, self.step)
        out = np.interp(positions, np.arange(len(data)), data)
        nxt = (positions[-1] + self.step) if len(positions) else self._pos
        keep_from = min(int(np.floor(nxt)), last)
        self._hist = data[keep_from:]
        self._pos = nxt - keep_from
        return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    return Resampler(src_rate, dst_rate).process(pcm)


class HighPass:
    """Filtr górnoprzepustowy (Butterworth 2. rzędu) do liczenia głośności mowy.

    Mikrofon USB na Pi ma silny infradźwięk (~12-23 Hz): w ciszy RMS ~1200,
    choć powyżej 150 Hz szum to ~70. Bez filtra VAD widział stałą "mowę".
    Filtrujemy tylko do pomiaru głośności; do STT idzie oryginalne audio.
    """

    def __init__(self, cutoff_hz: float = 150.0, rate: int = 16000, stages: int = 2):
        w0 = 2 * np.pi * cutoff_hz / rate
        alpha = np.sin(w0) / np.sqrt(2)
        cos = np.cos(w0)
        a0 = 1 + alpha
        self.b = np.array([(1 + cos) / 2, -(1 + cos), (1 + cos) / 2]) / a0
        self.a = np.array([1.0, -2 * cos / a0, (1 - alpha) / a0])
        # Dwa takie same stopnie = 4. rząd: infradźwięk ~20 Hz spada o ~70 dB.
        self.stages = stages
        self._zis = [np.zeros(2) for _ in range(stages)]
        try:
            from scipy.signal import lfilter  # type: ignore

            self._lfilter = lfilter
        except ImportError:
            self._lfilter = None

    def process(self, pcm: bytes) -> np.ndarray:
        y = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        for s in range(self.stages):
            y, self._zis[s] = self._stage(y, self._zis[s])
        return y

    def _stage(self, x: np.ndarray, zi: np.ndarray):
        if self._lfilter is not None:
            return self._lfilter(self.b, self.a, x, zi=zi)
        b0, b1, b2 = self.b
        _, a1, a2 = self.a
        z1, z2 = zi
        y = np.empty_like(x)
        for i, v in enumerate(x):  # postać transponowana II, jak lfilter
            out = b0 * v + z1
            z1 = b1 * v - a1 * out + z2
            z2 = b2 * v - a2 * out
            y[i] = out
        return y, np.array([z1, z2])

    def level(self, pcm: bytes) -> float:
        y = self.process(pcm)
        return float(np.sqrt(np.mean(y * y))) if len(y) else 0.0


def rms(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


def tone(freqs: list[tuple[float, float]], rate: int, volume: float = 0.3) -> bytes:
    """Krótki sygnał z listy (częstotliwość Hz, czas s), z łagodnym wejściem i wyjściem."""
    parts = []
    for freq, dur in freqs:
        t = np.arange(int(rate * dur)) / rate
        wave = np.sin(2 * np.pi * freq * t)
        fade = min(len(t) // 4, int(rate * 0.01))
        if fade:
            env = np.ones(len(t))
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
            wave *= env
        parts.append(wave)
    y = np.concatenate(parts) if parts else np.zeros(0)
    return (y * volume * 32767).astype(np.int16).tobytes()


# Sygnały generowane w kodzie, więc w repo nie ma plików binarnych.
# Własny plik sounds/<nazwa>.wav ma pierwszeństwo.
BUILTIN_SOUNDS = {
    "ding": [(880, 0.08), (1320, 0.10)],
    "koniec": [(660, 0.06)],
    "blad": [(330, 0.15), (220, 0.25)],
    "nie_slysze": [(440, 0.12), (330, 0.12)],
}
