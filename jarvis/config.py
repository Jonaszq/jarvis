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
        "input_device": None,                    # None = domyślne wejście PipeWire (USB PnP to jedyne źródło)
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
    "elevenlabs": {
        "voice_id": "pNInz6obpgDQGcFmaJgB",   # tymczasowy męski głos; wybór: scripts/voices.py
        "tts_model": "eleven_flash_v2_5",
        "stt_model": "scribe_v1",
        "language": "pl",
        "quota_switch": 0.95,                 # przy 95% limitu przechodzimy na Pipera
        "voice_settings": None,
    },
    "stt": {
        "engine": "elevenlabs",               # elevenlabs (zapas: whisper_local) | whisper_local
        "whisper_cli": "~/whisper.cpp/build/bin/whisper-cli",
        "whisper_model": "~/whisper.cpp/models/ggml-base.bin",  # wielojęzyczny, nie .en,
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
        "engine": "elevenlabs",               # elevenlabs (zapas: piper_local) | piper_local
        "piper_bin": ".venv/bin/piper",
        "voice": "models/pl_PL-darkman-medium.onnx",
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


def load_env_file(path: Path | str = "/etc/jarvis/env") -> None:
    """Wczytuje sekrety z pliku KLUCZ=wartość (systemd robi to sam, ręczne uruchomienie nie)."""
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


def secret(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None
