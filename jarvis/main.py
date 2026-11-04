"""Start Jarvisa.

  jarvis                 normalna praca: mikrofon, wake word, głośnik
  jarvis --text          pytania z klawiatury (bez mikrofonu), odpowiedź na głośnik
  jarvis --text --mute   pytania z klawiatury, odpowiedź tylko na ekranie
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time

from . import config as cfgmod
from .metrics import MetricsLog
from .orchestrator import Orchestrator, Utterance


def build_brain(cfg):
    from .brain.claude_code import ClaudeCodeBrain, build_command

    b = cfg["brain"]
    cmd = build_command(b["claude_bin"], b["model"], b.get("effort"), cfgmod.resolve(cfg, b["prompt_file"]))
    return ClaudeCodeBrain(cmd, cfgmod.resolve(cfg, b["workdir"]), timeout_s=b["timeout_s"],
                           warmup=b.get("warmup", False))


log = logging.getLogger("jarvis")
_EL_CLIENT = None


def elevenlabs_client(cfg):
    """Jeden klient (jedno połączenie) dla STT i TTS. None, gdy nie ma klucza."""
    global _EL_CLIENT
    key = cfgmod.secret("ELEVENLABS_API_KEY")
    if not key:
        return None
    if _EL_CLIENT is None:
        from .elevenlabs import ElevenLabsClient

        _EL_CLIENT = ElevenLabsClient(key, quota_switch=cfg["elevenlabs"]["quota_switch"])
    return _EL_CLIENT


def build_local_tts(cfg):
    from .tts.piper_local import PiperTTS

    t = cfg["tts"]
    return PiperTTS(cfgmod.resolve(cfg, t["voice"]), str(cfgmod.resolve(cfg, t["piper_bin"])),
                    t.get("mode", "auto"), t.get("sample_rate"))


def build_local_stt(cfg):
    from .stt.whisper_local import WhisperLocalSTT

    s = cfg["stt"]
    return WhisperLocalSTT(cfgmod.resolve(cfg, s["whisper_cli"]), cfgmod.resolve(cfg, s["whisper_model"]),
                           s["language"], s["threads"])


def build_tts(cfg):
    local = build_local_tts(cfg)
    client = elevenlabs_client(cfg) if cfg["tts"]["engine"] == "elevenlabs" else None
    if client is None:
        if cfg["tts"]["engine"] == "elevenlabs":
            log.warning("brak ELEVENLABS_API_KEY w /etc/jarvis/env, mówię Piperem")
        return local
    from .elevenlabs import ElevenLabsTTS
    from .fallback import FallbackTTS

    e = cfg["elevenlabs"]
    rate = cfg["audio"].get("output_rate", 22050)
    cloud = ElevenLabsTTS(client, e["voice_id"], e["tts_model"], e["language"], rate, e.get("voice_settings"))
    return FallbackTTS(cloud, local)


def build_stt(cfg):
    local = build_local_stt(cfg)
    client = elevenlabs_client(cfg) if cfg["stt"]["engine"] == "elevenlabs" else None
    if client is None:
        return local
    from .elevenlabs import ElevenLabsSTT
    from .fallback import FallbackSTT

    e = cfg["elevenlabs"]
    return FallbackSTT(ElevenLabsSTT(client, e["stt_model"], e["language"]), local)


class _NullPlayer:
    async def play(self, pcm, sample_rate):
        pass

    async def drain(self):
        pass

    async def play_sound(self, name):
        pass


class _TextSTT:
    async def transcribe(self, audio, sample_rate):
        return audio.decode("utf-8")


class _KeyboardListener:
    async def next_utterance(self, require_wake, timeout_s):
        if not require_wake:
            return None  # przy klawiaturze każde pytanie to nowa rozmowa
        line = await asyncio.get_running_loop().run_in_executor(None, lambda: input("ty> "))
        now = time.monotonic()
        return Utterance(line.encode("utf-8"), 0, now, now)


class _PrintingBrain:
    """Wypisuje odpowiedź na ekran, przepuszczając ją dalej bez zmian."""

    def __init__(self, inner):
        self.inner = inner

    async def start(self):
        await self.inner.start()

    async def reset(self):
        await self.inner.reset()

    async def close(self):
        await self.inner.close()

    async def ask(self, text):
        print("jarvis> ", end="", flush=True)
        async for chunk in self.inner.ask(text):
            print(chunk, end="", flush=True)
            yield chunk
        print()


class _MuteTTS:
    sample_rate = 16000

    async def start(self):
        pass

    async def close(self):
        pass

    async def synthesize(self, sentence):
        return
        yield b""


async def amain(args) -> None:
    cfgmod.load_env_file()
    cfg = cfgmod.load(args.config)
    a = cfg["audio"]
    metrics = MetricsLog(cfgmod.resolve(cfg, cfg["metrics"]["path"]))
    brain = build_brain(cfg)

    player = None
    if args.text and args.mute:
        player, tts = _NullPlayer(), _MuteTTS()
    else:
        from .audio.player import SoundDevicePlayer

        tts = build_tts(cfg)
        player = SoundDevicePlayer(rate=a.get("output_rate", 22050), device=a["output_device"],
                                   sounds_dir=cfgmod.resolve(cfg, cfg["sounds_dir"]))
        player.start()

    loop = asyncio.get_running_loop()
    bt_task = None
    if a.get("bt_speaker") and not args.mute:
        from .audio.bluetooth import keep_connected

        bt_task = asyncio.create_task(keep_connected(a["bt_speaker"]))
    if args.text:
        listener, stt, brain = _KeyboardListener(), _TextSTT(), _PrintingBrain(brain)
    else:
        from .audio.listener import MicListener, OpenWakeWord, VadConfig

        w, v = cfg["wakeword"], cfg["vad"]
        listener = MicListener(
            OpenWakeWord(w["model"]), device=a["input_device"], input_rate=a["input_rate"],
            rate=a["rate"], block_ms=a["block_ms"], preroll_s=a["preroll_s"],
            wake_threshold=w["threshold"],
            vad=VadConfig(v["silence_ms"], v["energy_threshold"], v["max_record_s"], v["no_speech_timeout_s"]),
            on_wake=lambda: asyncio.ensure_future(player.play_sound("ding")),
        )
        listener.start(loop)
        stt = build_stt(cfg)

    orch = Orchestrator(listener, stt, brain, tts, player, metrics,
                        followup_s=cfg["followup_s"], reset_after_s=cfg["brain"]["reset_after_s"])
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    task = asyncio.create_task(orch.run())
    try:
        done, _ = await asyncio.wait({task, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()
        else:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, EOFError):
                pass
    finally:
        if bt_task:
            bt_task.cancel()
        if _EL_CLIENT is not None:
            await _EL_CLIENT.close()
        # Bez tego program kończył się, zanim głośnik dograł ostatnie zdanie.
        if hasattr(player, "stop"):
            await player.drain()
            await asyncio.get_running_loop().run_in_executor(None, player.stop)


def run() -> None:
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument("--config", default=None, help="ścieżka do config.yaml")
    parser.add_argument("--text", action="store_true", help="pytania z klawiatury zamiast mikrofonu")
    parser.add_argument("--mute", action="store_true", help="z --text: bez głośnika i TTS")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        asyncio.run(amain(args))
    except (KeyboardInterrupt, EOFError):
        pass


if __name__ == "__main__":
    run()
