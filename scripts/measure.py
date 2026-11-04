#!/usr/bin/env python3
"""Pomiary etapu 1: ile trwa każdy krok osobno, na prawdziwym Pi.

  python scripts/measure.py piper        ładowanie głosu i synteza dwóch zdań
  python scripts/measure.py whisper      rozpoznanie zdania nagranego Piperem
  python scripts/measure.py claude-cold  stary sposób: `claude -p` od zera
  python scripts/measure.py claude-warm  ciepły proces stream-json, 3 pytania
  python scripts/measure.py mic          poziomy szumu i mowy z mikrofonu (strojenie VAD)
  python scripts/measure.py all          wszystko oprócz mic

Wyniki idą na ekran i do logs/measure.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import config as cfgmod  # noqa: E402
from jarvis.audio.dsp import Resampler, rms  # noqa: E402

SENTENCES = [
    "Dzień dobry, w Gorzowie jest dziś sześć stopni i lekki deszcz.",
    "Masz dwa spotkania, pierwsze o dziesiątej.",
]
QUESTIONS = ["Ile to jest dwa plus dwa?", "Podaj stolicę Francji.", "Jaki jest dzień tygodnia po środzie?"]


def out(name: str, result: dict) -> dict:
    result = {"test": name, "ts": round(time.time(), 1), **result}
    print(json.dumps(result, ensure_ascii=False))
    log = ROOT / "logs" / "measure.jsonl"
    log.parent.mkdir(exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


async def m_piper(cfg) -> tuple[dict, bytes, int]:
    from jarvis.main import build_tts

    tts = build_tts(cfg)
    t0 = time.monotonic()
    await tts.start()
    load = time.monotonic() - t0
    runs = []
    pcm_first = b""
    for s in SENTENCES:
        t0 = time.monotonic()
        pcm = b"".join([c async for c in tts.synthesize(s)])
        took = time.monotonic() - t0
        audio_s = len(pcm) / 2 / tts.sample_rate
        runs.append({"synth_s": round(took, 3), "audio_s": round(audio_s, 2), "rtf": round(took / audio_s, 3)})
        pcm_first = pcm_first or pcm
    await tts.close()
    return out("piper", {"mode": tts.mode, "load_s": round(load, 3), "runs": runs}), pcm_first, tts.sample_rate


async def m_whisper(cfg, pcm: bytes | None = None, rate: int | None = None) -> dict:
    from jarvis.main import build_stt

    if pcm is None:
        _, pcm, rate = await m_piper(cfg)
    pcm16 = Resampler(rate, 16000).process(pcm)
    stt = build_stt(cfg)
    runs = []
    for _ in range(2):
        t0 = time.monotonic()
        text = await stt.transcribe(pcm16, 16000)
        runs.append({"s": round(time.monotonic() - t0, 3), "text": text})
    return out("whisper", {"model": Path(cfg["stt"]["whisper_model"]).name,
                           "audio_s": round(len(pcm16) / 32000, 2), "expected": SENTENCES[0], "runs": runs})


def m_claude_cold(cfg) -> dict:
    b = cfg["brain"]
    cmd = [b["claude_bin"], "-p", QUESTIONS[0], "--model", b["model"]]
    runs = []
    for _ in range(2):
        t0 = time.monotonic()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           cwd=str(cfgmod.resolve(cfg, b["workdir"]).expanduser()))
        runs.append({"s": round(time.monotonic() - t0, 3), "rc": p.returncode, "answer": p.stdout.strip()[:120]})
    return out("claude_cold", {"cmd": " ".join(cmd[:1] + ["-p", "…"] + cmd[3:]), "runs": runs})


async def m_claude_warm(cfg) -> dict:
    from jarvis.main import build_brain

    cfgmod.resolve(cfg, cfg["brain"]["workdir"]).expanduser().mkdir(parents=True, exist_ok=True)
    brain = build_brain(cfg)
    t0 = time.monotonic()
    await brain.start()
    spawn = time.monotonic() - t0
    runs = []
    for q in QUESTIONS:
        t0 = time.monotonic()
        first = None
        answer = []
        async for chunk in brain.ask(q):
            first = first or time.monotonic() - t0
            answer.append(chunk)
        runs.append({"first_token_s": round(first or -1, 3), "total_s": round(time.monotonic() - t0, 3),
                     "answer": "".join(answer)[:120]})
    await brain.close()
    firsts = [r["first_token_s"] for r in runs]
    return out("claude_warm", {"spawn_s": round(spawn, 3), "runs": runs,
                               "first_token_median_s": round(statistics.median(firsts), 3),
                               "note": "pierwsze pytanie zawiera rozgrzewkę procesu"})


def m_mic(cfg, seconds: float = 8.0) -> dict:
    import numpy as np
    import sounddevice as sd

    from jarvis.audio.listener import find_input_device

    a = cfg["audio"]
    idx, native = find_input_device(a["input_device"])
    rate = a["input_rate"] or native
    print(f"Nagrywam {seconds:.0f} s z urządzenia {idx} ({rate} Hz). "
          "Przez pierwsze 3 s cisza, potem powiedz coś normalnym głosem z miejsca, gdzie zwykle siedzisz.")
    rec = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16", device=idx)
    sd.wait()
    pcm = Resampler(rate, 16000).process(rec.tobytes())
    block = 1280 * 2
    levels = [rms(pcm[i:i + block]) for i in range(0, len(pcm) - block, block)]
    quiet = levels[: int(3 / 0.08)]
    speech = levels[int(3 / 0.08):]
    noise = float(np.percentile(quiet, 90)) if quiet else 0.0
    voice = float(np.percentile(speech, 80)) if speech else 0.0
    suggestion = round((noise * voice) ** 0.5) if noise and voice > noise else None
    return out("mic", {"device": idx, "native_rate": native, "noise_p90": round(noise), "speech_p80": round(voice),
                       "suggested_energy_threshold": suggestion,
                       "current_energy_threshold": cfg["vad"]["energy_threshold"]})


async def amain(args) -> None:
    cfg = cfgmod.load(args.config)
    what = args.what
    if what in ("piper", "all"):
        res, pcm, rate = await m_piper(cfg)
        if what == "all":
            await m_whisper(cfg, pcm, rate)
    if what == "whisper":
        await m_whisper(cfg)
    if what in ("claude-cold", "all"):
        m_claude_cold(cfg)
    if what in ("claude-warm", "all"):
        await m_claude_warm(cfg)
    if what == "mic":
        m_mic(cfg)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["piper", "whisper", "claude-cold", "claude-warm", "mic", "all"])
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = p.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
