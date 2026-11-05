import httpx
import pytest

from jarvis.tts.azure import AzureError, AzureTTS


async def test_azure_streams_pcm_and_escapes_ssml():
    seen = {}

    def handler(req):
        seen["url"], seen["body"], seen["h"] = str(req.url), req.content.decode(), req.headers
        return httpx.Response(200, content=b"\x01\x02\x03\x04\x05")

    tts = AzureTTS("k", "westeurope", transport=httpx.MockTransport(handler))
    out = b"".join([c async for c in tts.synthesize("Tom & Jerry <3")])
    assert out == b"\x01\x02\x03\x04"  # tylko pełne próbki
    assert seen["url"] == "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    assert 'xml:lang="pl-PL"' in seen["body"] and "pl-PL-MarekNeural" in seen["body"]
    assert "Tom &amp; Jerry &lt;3" in seen["body"]
    assert seen["h"]["X-Microsoft-OutputFormat"] == "raw-24khz-16bit-mono-pcm"
    assert tts.chars == len("Tom & Jerry <3")


async def test_azure_429_is_quota():
    tts = AzureTTS("k", "westeurope", transport=httpx.MockTransport(lambda r: httpx.Response(429, content=b"limit")))
    with pytest.raises(AzureError) as e:
        async for _ in tts.synthesize("x"):
            pass
    assert e.value.quota
