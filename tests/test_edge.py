import shutil

import pytest

from jarvis.tts.edge import EdgeError, EdgeTTS, mp3_to_pcm


async def test_edge_errors_become_edgeerror(monkeypatch):
    tts = EdgeTTS()

    async def boom(sentence):
        raise ConnectionError("403")

    monkeypatch.setattr(tts, "_mp3", boom)
    with pytest.raises(EdgeError):
        async for _ in tts.synthesize("x"):
            pass


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="brak ffmpeg")
async def test_mp3_decode_roundtrip(tmp_path):
    import asyncio

    p = await asyncio.create_subprocess_exec("ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=0.5",
                                             "-f", "mp3", "pipe:1", stdout=asyncio.subprocess.PIPE)
    mp3, _ = await p.communicate()
    pcm = await mp3_to_pcm(mp3, 24000)
    assert 22000 < len(pcm) < 26000 * 2 * 0.6  # ~0,5 s, 16-bit mono
