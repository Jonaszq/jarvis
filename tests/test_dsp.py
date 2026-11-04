import numpy as np

from jarvis.audio.dsp import Resampler, rms, tone


def sine(rate, freq=440, secs=1.0):
    t = np.arange(int(rate * secs)) / rate
    return (np.sin(2 * np.pi * freq * t) * 10000).astype(np.int16)


def test_streaming_resample_length_and_continuity():
    for src in (44100, 48000, 22050):
        x = sine(src).tobytes()
        rs = Resampler(src, 16000)
        out = b"".join(rs.process(x[i:i + 2000]) for i in range(0, len(x), 2000))
        y = np.frombuffer(out, dtype=np.int16).astype(float)
        assert abs(len(y) - 16000) < 40, (src, len(y))
        # Ton 440 Hz zostaje tonem 440 Hz, z tą samą głośnością.
        spectrum = np.abs(np.fft.rfft(y[500:-500]))
        peak_hz = np.argmax(spectrum) * 16000 / len(y[500:-500])
        assert abs(peak_hz - 440) < 3, (src, peak_hz)
        assert 6500 < rms(out[2000:-2000]) < 7600


def test_aliasing_removed_when_downsampling():
    # 12 kHz nie mieści się w 16 kHz (Nyquist 8 kHz), więc ma zostać wycięte.
    out = Resampler(48000, 16000).process(sine(48000, freq=12000).tobytes())
    assert rms(out[2000:-2000]) < 300


def test_same_rate_passthrough():
    x = sine(16000).tobytes()
    assert Resampler(16000, 16000).process(x) == x


def test_rms_and_tone():
    assert rms(b"") == 0.0
    assert rms(tone([(440, 0.1)], 16000)) > 1000
