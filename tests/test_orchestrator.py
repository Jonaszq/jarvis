import asyncio

from jarvis.metrics import MetricsLog
from jarvis.orchestrator import Orchestrator, Utterance


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeListener:
    def __init__(self, utterances):
        self.utterances = list(utterances)
        self.calls = []

    async def next_utterance(self, require_wake, timeout_s):
        self.calls.append(require_wake)
        return self.utterances.pop(0) if self.utterances else None


class FakeSTT:
    def __init__(self, clock):
        self.clock = clock

    async def transcribe(self, audio, sample_rate):
        self.clock.t += 0.3
        return audio.decode()


class FakeBrain:
    def __init__(self, clock, chunks=("Dziś ", "jest ładnie. ", "Weź ", "kurtkę."), fail=False):
        self.clock, self.chunks, self.fail = clock, chunks, fail
        self.asked, self.resets = [], 0

    async def start(self): pass
    async def close(self): pass

    async def reset(self):
        self.resets += 1

    async def ask(self, text):
        self.asked.append(text)
        if self.fail:
            raise RuntimeError("padło")
        for c in self.chunks:
            self.clock.t += 0.2
            yield c


class FakeTTS:
    sample_rate = 22050

    def __init__(self, clock):
        self.clock, self.said = clock, []

    async def start(self): pass
    async def close(self): pass

    async def synthesize(self, sentence):
        self.said.append(sentence)
        self.clock.t += 0.1
        yield sentence.encode()


class FakePlayer:
    def __init__(self):
        self.played, self.sounds = [], []

    async def play(self, pcm, sample_rate):
        self.played.append(pcm)

    async def drain(self): pass

    async def play_sound(self, name):
        self.sounds.append(name)


def setup(utterances, tmp_path, **brain_kw):
    clock = Clock()
    parts = dict(listener=FakeListener(utterances), stt=FakeSTT(clock), brain=FakeBrain(clock, **brain_kw),
                 tts=FakeTTS(clock), player=FakePlayer())
    orch = Orchestrator(**parts, metrics=MetricsLog(tmp_path / "turns.jsonl"), clock=clock, reset_after_s=300)
    return orch, parts, clock


async def test_turn_speaks_sentence_by_sentence_and_measures(tmp_path):
    orch, p, clock = setup([Utterance(b"jaka pogoda", 16000, 98.0, 100.0)], tmp_path)
    await orch.run_once()
    assert p["tts"].said == ["Dziś jest ładnie.", "Weź kurtkę."]
    assert p["listener"].calls == [True, False]  # po odpowiedzi czeka na dopytanie bez wake worda
    import json
    rec = json.loads((tmp_path / "turns.jsonl").read_text())
    d = rec["since_speech_end"]
    assert d["transcript"] == 0.3
    assert d["first_token"] < d["first_sentence"] <= d["first_audio"] < d["done"]
    assert rec["heard"] == "jaka pogoda" and rec["answer"] == "Dziś jest ładnie. Weź kurtkę."


async def test_followup_without_wake(tmp_path):
    us = [Utterance(b"pierwsze", 16000, 1.0, 100.0), Utterance(b"drugie", 16000, None, 105.0)]
    orch, p, _ = setup(us, tmp_path)
    await orch.run_once()
    assert p["brain"].asked == ["pierwsze", "drugie"]
    assert p["listener"].calls == [True, False, False]


async def test_empty_transcript_plays_sound(tmp_path):
    orch, p, _ = setup([Utterance(b"  ", 16000, 1.0, 100.0)], tmp_path)
    await orch.run_once()
    assert p["brain"].asked == [] and p["player"].sounds == ["nie_slysze"]


async def test_brain_error_does_not_crash(tmp_path):
    orch, p, _ = setup([Utterance(b"x", 16000, 1.0, 100.0)], tmp_path, fail=True)
    await orch.run_once()
    assert p["player"].sounds == ["blad"]
    assert "padło" in (tmp_path / "turns.jsonl").read_text()


async def test_reset_after_long_silence(tmp_path):
    orch, p, clock = setup([Utterance(b"a", 16000, 1.0, 100.0)], tmp_path)
    await orch.run_once()
    clock.t += 301
    p["listener"].utterances = [Utterance(b"b", 16000, clock.t, clock.t)]
    await orch.run_once()
    assert p["brain"].resets == 1
