import asyncio

import numpy as np

from jarvis.audio.listener import MicListener, NoiseFloor, VadConfig


def block(level, n=1280):
    rng = np.random.default_rng(int(level))
    return (rng.standard_normal(n) * level).astype(np.int16).tobytes()


def test_noise_floor_tracks_background_and_ignores_speech():
    nf = NoiseFloor(initial=200)
    for _ in range(300):
        nf.update(540)
    assert 500 < nf.level < 560
    for _ in range(20):
        nf.update(5000)  # mowa nie podbija szumu
    assert nf.level < 600
    assert NoiseFloor(100).threshold(VadConfig(energy_threshold=400)) == 400
    assert nf.threshold(VadConfig(energy_threshold=400)) > 1300


class NeverWake:
    def score(self, b):
        return 0.0

    def reset(self):
        pass


async def test_recording_ends_on_silence_despite_constant_hiss():
    lst = MicListener(NeverWake(), vad=VadConfig(silence_ms=400, energy_threshold=400, max_record_s=12))
    for _ in range(200):
        lst.noise.update(540)  # syk z kabla audio, jak na Pi
    blocks = [block(3000)] * 10 + [block(540)] * 20  # 0,8 s mowy, potem tylko syk
    for b in blocks:
        lst._q.put_nowait(b)
    utt = await lst._record(wake_t=1.0, start_timeout_s=4)
    assert utt is not None
    assert len(utt.audio) < len(b"".join(blocks))  # skończyło się na ciszy, nie na limicie
