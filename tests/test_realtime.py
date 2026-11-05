import asyncio
import base64
import json

import pytest
from websockets.asyncio.server import serve

from jarvis.orchestrator import Utterance
from jarvis.stt.realtime import ScribeRealtime


async def fake_scribe(ws, received, fail=False):
    assert ws.request.headers["xi-api-key"] == "k"
    await ws.send(json.dumps({"message_type": "session_started", "session_id": "s"}))
    audio = b""
    async for raw in ws:
        msg = json.loads(raw)
        assert msg["message_type"] == "input_audio_chunk"
        audio += base64.b64decode(msg["audio_base_64"])
        await ws.send(json.dumps({"message_type": "partial_transcript", "text": "jaka"}))
        if msg.get("commit"):
            received.append(audio)
            if fail:
                await ws.send(json.dumps({"message_type": "auth_error", "error": "bad key"}))
            else:
                await ws.send(json.dumps({"message_type": "committed_transcript", "text": " jaka  pogoda "}))


@pytest.fixture
async def server():
    received = []
    state = {"fail": False}
    async with serve(lambda ws: fake_scribe(ws, received, state["fail"]), "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}/rt", received, state


async def test_streams_audio_and_returns_committed_text(server):
    url, received, _ = server
    stt = ScribeRealtime("k", url=url)
    s = stt.session()
    s.feed(b"\x01\x00" * 100)  # zanim połączenie wstanie: ma poczekać w kolejce
    await asyncio.sleep(0.05)
    s.feed(b"\x02\x00" * 100)
    assert await s.finish() == "jaka pogoda"
    assert received[0].startswith(b"\x01\x00" * 100 + b"\x02\x00" * 100)
    assert "model_id=scribe_v2_realtime" in stt.url and "commit_strategy=manual" in stt.url


async def test_server_error_raises(server):
    url, _, state = server
    state["fail"] = True
    s = ScribeRealtime("k", url=url).session()
    s.feed(b"\x00\x00" * 10)
    with pytest.raises(RuntimeError):
        await s.finish()


async def test_unreachable_server_raises_fast():
    s = ScribeRealtime("k", url="ws://127.0.0.1:9/none").session()
    with pytest.raises(Exception):
        await s.finish(timeout=3)


async def test_orchestrator_uses_stream_then_falls_back(tmp_path):
    from tests.test_orchestrator import setup

    ok = asyncio.get_running_loop().create_future()
    ok.set_result("z prądem")
    bad = asyncio.get_running_loop().create_future()
    bad.set_exception(RuntimeError("ws padł"))
    orch, p, _ = setup([Utterance(b"z nagrania", 16000, 1.0, 100.0, ok),
                        Utterance(b"z nagrania", 16000, None, 105.0, bad)], tmp_path)
    await orch.run_once()
    assert p["brain"].asked == ["z prądem", "z nagrania"]
