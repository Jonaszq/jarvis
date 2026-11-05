import json

import httpx
import pytest

from jarvis.elevenlabs import ElevenLabsClient, ElevenLabsError, ElevenLabsSTT, ElevenLabsTTS
from jarvis.fallback import FallbackSTT, FallbackTTS


def client_with(handler, **kw):
    return ElevenLabsClient("test-key", transport=httpx.MockTransport(handler), **kw)


def usage(used, limit):
    return httpx.Response(200, json={"character_count": used, "character_limit": limit})


async def collect(gen):
    return b"".join([c async for c in gen])


async def test_tts_streams_pcm_and_keeps_samples_whole():
    seen = {}

    def handler(req: httpx.Request):
        if req.url.path == "/v1/user/subscription":
            return usage(100, 10000)
        seen["path"], seen["params"], seen["body"] = req.url.path, dict(req.url.params), json.loads(req.content)
        assert req.headers["xi-api-key"] == "test-key"
        return httpx.Response(200, content=b"\x01\x02\x03\x04\x05")  # nieparzysta długość

    c = client_with(handler)
    tts = ElevenLabsTTS(c, "VOICE", sample_rate=22050)
    await tts.start()
    pcm = await collect(tts.synthesize("Cześć."))
    assert len(pcm) % 2 == 0
    assert seen["path"] == "/v1/text-to-speech/VOICE/stream"
    assert seen["params"] == {"output_format": "pcm_22050"}
    assert seen["body"]["model_id"] == "eleven_flash_v2_5" and seen["body"]["language_code"] == "pl"
    assert c.usage == (106, 10000)
    await c.close()


async def test_quota_error_is_recognised():
    def handler(req):
        if req.url.path == "/v1/user/subscription":
            return httpx.Response(401, json={"detail": {"status": "missing_permissions"}})
        return httpx.Response(401, json={"detail": {"status": "quota_exceeded", "message": "limit"}})

    c = client_with(handler)
    await c.start()
    with pytest.raises(ElevenLabsError) as err:
        await collect(ElevenLabsTTS(c, "V").synthesize("x"))
    assert err.value.quota
    await c.close()


async def test_soft_limit_stops_before_request():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return usage(9600, 10000)

    c = client_with(handler, quota_switch=0.95)
    await c.start()
    with pytest.raises(ElevenLabsError) as err:
        await collect(ElevenLabsTTS(c, "V").synthesize("x"))
    assert err.value.quota and calls == ["/v1/user/subscription"]
    await c.close()


async def test_stt_sends_wav_and_returns_text():
    def handler(req):
        if req.url.path == "/v1/user/subscription":
            return usage(0, 10)
        body = req.content
        assert b'name="model_id"' in body and b"scribe_v1" in body and b"RIFF" in body
        return httpx.Response(200, json={"text": "  jaka  pogoda  "})

    c = client_with(handler)
    assert await ElevenLabsSTT(c).transcribe(b"\x00\x00" * 160, 16000) == "jaka pogoda"
    await c.close()


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class FakeTTS:
    def __init__(self, name, rate, fail=None, partial=False):
        self.name, self.sample_rate, self.fail, self.partial = name, rate, fail, partial
        self.started = 0
        self.calls = 0

    async def start(self):
        self.started += 1

    async def close(self):
        pass

    async def synthesize(self, s):
        self.calls += 1
        if self.fail and self.partial:
            yield b"pp"
        if self.fail:
            raise self.fail
        yield f"{self.name}:{s}".encode()


async def test_fallback_tts_switches_and_recovers():
    clock = Clock()
    primary = FakeTTS("el", 22050, fail=ElevenLabsError("down", 503))
    backup = FakeTTS("piper", 16000)
    tts = FallbackTTS(primary, backup, error_cooldown_s=60, clock=clock)
    await tts.start()
    assert await collect(tts.synthesize("a")) == b"piper:a"
    assert tts.sample_rate == 16000 and tts.last_engine == "backup"
    assert await collect(tts.synthesize("b")) == b"piper:b"
    assert primary.calls == 1  # w przerwie chmury nie męczymy
    primary.fail = None
    clock.t = 61
    assert await collect(tts.synthesize("c")) == b"el:c"
    assert tts.sample_rate == 22050 and tts.last_engine == "primary"
    assert backup.started == 1


async def test_fallback_quota_pauses_longer():
    clock = Clock()
    primary = FakeTTS("el", 22050, fail=ElevenLabsError("koniec", 401, "quota_exceeded"))
    tts = FallbackTTS(primary, FakeTTS("piper", 22050), error_cooldown_s=60, quota_cooldown_s=3600, clock=clock)
    await collect(tts.synthesize("a"))
    clock.t = 120
    await collect(tts.synthesize("b"))
    assert primary.calls == 1


async def test_fallback_does_not_repeat_half_spoken_sentence():
    primary = FakeTTS("el", 22050, fail=ElevenLabsError("cut", 500), partial=True)
    tts = FallbackTTS(primary, FakeTTS("piper", 22050))
    assert await collect(tts.synthesize("a")) == b"pp"


async def test_fallback_stt():
    class S:
        def __init__(self, out=None, fail=None):
            self.out, self.fail = out, fail

        async def transcribe(self, audio, rate):
            if self.fail:
                raise self.fail
            return self.out

    stt = FallbackSTT(S(fail=OSError("no net")), S(out="lokalnie"))
    assert await stt.transcribe(b"", 16000) == "lokalnie" and stt.last_engine == "backup"
