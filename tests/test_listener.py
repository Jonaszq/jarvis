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
    assert 900 < nf.threshold(VadConfig(energy_threshold=400)) <= 1200
    loud = NoiseFloor(initial=2000)
    assert loud.threshold(VadConfig(energy_threshold=400)) == 1200  # limit: głos nie ginie w szumie


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


class FakeSession:
    def __init__(self):
        self.fed, self.aborted = [], False

    def feed(self, b):
        self.fed.append(b)

    async def finish(self):
        return "tekst"

    def abort(self):
        self.aborted = True


class FakeStreamer:
    def __init__(self):
        self.sessions = []

    def session(self):
        self.sessions.append(FakeSession())
        return self.sessions[-1]


async def test_followup_opens_stream_only_when_speech_starts():
    st = FakeStreamer()
    lst = MicListener(NeverWake(), vad=VadConfig(silence_ms=400, energy_threshold=400), streamer=st)
    for b in [block(100)] * 10 + [block(3000)] * 5 + [block(100)] * 10:
        lst._q.put_nowait(b)
    utt = await lst._record(wake_t=None, start_timeout_s=5)
    assert len(st.sessions) == 1 and await utt.transcript == "tekst"
    assert len(st.sessions[0].fed) < 25  # cisza przed mową nie poszła (poza ~4 blokami zapasu)


async def test_silence_aborts_stream():
    st = FakeStreamer()
    lst = MicListener(NeverWake(), vad=VadConfig(energy_threshold=400), streamer=st)
    for b in [block(100)] * 60:
        lst._q.put_nowait(b)
    assert await lst._record(wake_t=1.0, start_timeout_s=4) is None
    assert st.sessions[0].aborted


async def test_quiet_word_endings_do_not_cut_recording():
    lst = MicListener(NeverWake(), vad=VadConfig(silence_ms=400, energy_threshold=400))
    for _ in range(200):
        lst.noise.update(300)  # próg ~540
    speech = [block(2000)] * 5 + [block(420)] * 8 + [block(2000)] * 5  # cicha końcówka/pauza w środku
    for b in speech + [block(100)] * 10:
        lst._q.put_nowait(b)
    utt = await lst._record(wake_t=1.0, start_timeout_s=4)
    assert len(utt.audio) >= len(b"".join(speech))  # druga część zdania nie ucięta
