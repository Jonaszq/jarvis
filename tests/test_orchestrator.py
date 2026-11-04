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


async def test_end_command_stops_followup_without_brain(tmp_path):
    us = [Utterance(b"pytanie", 16000, 1.0, 100.0), Utterance("Dzięki, to wszystko.".encode(), 16000, None, 105.0),
          Utterance(b"to nie powinno dojsc", 16000, None, 110.0)]
    orch, p, _ = setup(us, tmp_path)
    await orch.run_once()
    assert p["brain"].asked == ["pytanie"]
    assert p["tts"].said[-1] == "Jasne."
    assert p["listener"].calls == [True, False]


async def test_sleep_ignores_everything_until_wake_phrase(tmp_path):
    orch, p, _ = setup([Utterance("Jarvis, wyłącz się.".encode(), 16000, 1.0, 100.0)], tmp_path)
    await orch.run_once()
    assert orch.sleeping and p["brain"].asked == []
    said = len(p["tts"].said)
    p["listener"].utterances = [Utterance(b"jaka jest pogoda", 16000, 2.0, 200.0)]
    await orch.run_once()
    assert p["brain"].asked == [] and len(p["tts"].said) == said  # cisza, zero kosztów mózgu
    p["listener"].utterances = [Utterance("Hej Jarvis, obudź się!".encode(), 16000, 3.0, 300.0),
                                Utterance(b"jaka jest pogoda", 16000, None, 305.0)]
    await orch.run_once()
    assert not orch.sleeping
    assert p["tts"].said[said] == "Jestem."
    assert p["brain"].asked == ["jaka jest pogoda"]


async def test_forget_command_uses_journal(tmp_path):
    from datetime import datetime

    from jarvis.journal import TZ, Journal

    clock = Clock()
    j = Journal(tmp_path, clock=lambda: datetime(2026, 11, 4, 20, tzinfo=TZ))
    j.add("zły humor", "mood")
    brain, tts = FakeBrain(clock), FakeTTS(clock)
    orch = Orchestrator(FakeListener([Utterance(b"zapomnij to", 16000, 1.0, 1.0)]), FakeSTT(clock), brain, tts,
                        FakePlayer(), clock=clock, journal=j)
    await orch.run_once()
    assert j.entries(datetime(2026, 11, 4).date()) == [] and brain.asked == []
    assert tts.said == ["Jasne, zapomniałem."]


async def test_empty_transcript_ends_conversation(tmp_path):
    utts = [Utterance(b"", 16000, 1.0, 1.0), Utterance(b"pytanie", 16000, None, 2.0)]
    orch, parts, clock = setup(utts, tmp_path)
    await orch.run_once()
    assert parts["listener"].calls == [True]  # bez dopytania po pustej turze
    assert parts["player"].sounds == ["nie_slysze"]
