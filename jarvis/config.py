"""Konfiguracja z config.yaml (ustawienia) i zmiennych środowiska (sekrety).

Sekrety (np. ELEVENLABS_API_KEY) nigdy nie trafiają do config.yaml ani do
repo. Na Pi systemd wczytuje je z /etc/jarvis/env.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "audio": {
        "input_device": "USB PnP Audio Device",  # fragment nazwy urządzenia
        "input_rate": None,                      # None = natywna stawka mikrofonu
        "rate": 16000,                           # do tego resamplujemy
        "block_ms": 80,                          # openWakeWord lubi 80 ms
        "preroll_s": 1.0,                        # bufor sprzed wake worda
        "output_device": None,                   # None = domyślne (JBL przez PipeWire)
        "output_rate": 22050,                    # tyle daje Piper medium i ElevenLabs pcm_22050
    },
    "wakeword": {
        "model": "hey_jarvis",
        "threshold": 0.5,
        "cooldown_s": 1.5,
    },
    "vad": {
        "silence_ms": 700,       # etap 1: prosty próg energii; Silero w etapie 4
        "energy_threshold": 500, # RMS dla int16; stroimy pod mikrofon
        "max_record_s": 12,
        "no_speech_timeout_s": 4,
    },
    "followup_s": 5,             # okno na dopytanie bez wake worda
    "stt": {
        "engine": "whisper_local",
        "whisper_cli": "~/whisper.cpp/build/bin/whisper-cli",
        "whisper_model": "~/whisper.cpp/models/ggml-base.bin",
        "language": "pl",
        "threads": 4,
    },
    "brain": {
        "engine": "claude_code",
        "claude_bin": "claude",
        "model": "haiku",
        "effort": "low",
        "prompt_file": "brain/jarvis_prompt.md",
        "workdir": "~/.jarvis-brain",   # pusty katalog, żeby CLI nie czytało repo
        "reset_after_s": 300,
        "timeout_s": 30,
        "warmup": False,
    },
    "tts": {
        "engine": "piper_local",
        "piper_bin": "piper",
        "voice": "~/piper/voices/pl_PL-gosia-medium.onnx",
        "sample_rate": None,            # None = z pliku .onnx.json
        "mode": "auto",                 # auto | python (piper-tts) | cli (binarka piper)
    },
    "metrics": {"path": "~/jarvis/logs/turns.jsonl"},
    "sounds_dir": "sounds",
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: Path | str | None = None) -> dict[str, Any]:
    path = Path(path or os.environ.get("JARVIS_CONFIG", "config.yaml")).expanduser()
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = _merge(DEFAULTS, data)
    cfg["_root"] = str(Path(__file__).resolve().parent.parent)
    return cfg


def resolve(cfg: dict[str, Any], value: str | None) -> Path | None:
    """Rozwija ~ i ścieżki względne (względem katalogu repo)."""
    if value is None:
        return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = Path(cfg["_root"]) / p
    return p


def secret(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None
