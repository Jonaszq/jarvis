#!/usr/bin/env python3
"""Wybór głosu ElevenLabs: próbki do odsłuchania w plikach, nie na głośniku.

  python scripts/voices.py mine  [--out DIR] [--max 6]
      męskie głosy z Twojego konta; każdy mówi po polsku to samo zdanie
      (kosztuje ~100 kredytów za głos)
  python scripts/voices.py polish [--out DIR] [--max 8]
      męskie polskie głosy z biblioteki ElevenLabs; pobiera ich darmowe
      próbki (mp3), nic nie kosztuje
  python scripts/voices.py add PUBLIC_OWNER_ID VOICE_ID "Nazwa"
      dodaje głos z biblioteki do konta, żeby Jarvis mógł go używać

Wybrany voice_id wpisujesz w config.yaml (elevenlabs: voice_id: ...).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from jarvis import config as cfgmod  # noqa: E402
from jarvis.elevenlabs import API, pcm_to_wav  # noqa: E402

TEXT = "Cześć Jonasz, tu Jarvis. W Gorzowie jest dziś sześć stopni, a o dziesiątej masz spotkanie."


def slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()[:40] or "glos"


async def mine(http: httpx.AsyncClient, out: Path, limit: int, model: str) -> None:
    r = await http.get("/v1/voices")
    r.raise_for_status()
    voices = [v for v in r.json().get("voices", []) if (v.get("labels") or {}).get("gender") == "male"]
    print(f"męskie głosy na koncie: {len(voices)}, robię próbki dla {min(limit, len(voices))}")
    for v in voices[:limit]:
        resp = await http.post(f"/v1/text-to-speech/{v['voice_id']}", params={"output_format": "pcm_22050"},
                               json={"text": TEXT, "model_id": model, "language_code": "pl"}, timeout=60)
        if resp.status_code >= 400:
            print(f"  {v['name']}: błąd {resp.status_code} {resp.text[:120]}")
            continue
        path = out / f"el_{slug(v['name'])}.wav"
        path.write_bytes(pcm_to_wav(resp.content, 22050))
        labels = v.get("labels") or {}
        print(json.dumps({"file": path.name, "name": v["name"], "voice_id": v["voice_id"],
                          "accent": labels.get("accent"), "age": labels.get("age"),
                          "opis": labels.get("description") or labels.get("descriptive")}, ensure_ascii=False))


async def polish(http: httpx.AsyncClient, out: Path, limit: int) -> None:
    r = await http.get("/v1/shared-voices", params={"language": "pl", "gender": "male",
                                                    "page_size": limit, "sort": "trending"})
    r.raise_for_status()
    voices = r.json().get("voices", [])
    print(f"polskie męskie głosy z biblioteki: {len(voices)}")
    for v in voices[:limit]:
        url = v.get("preview_url")
        if not url:
            continue
        async with httpx.AsyncClient(timeout=30) as plain:  # próbki leżą na publicznym CDN
            audio = await plain.get(url)
        path = out / f"pl_{slug(v.get('name', 'glos'))}.mp3"
        path.write_bytes(audio.content)
        print(json.dumps({"file": path.name, "name": v.get("name"), "voice_id": v.get("voice_id"),
                          "public_owner_id": v.get("public_owner_id"), "age": v.get("age"),
                          "opis": (v.get("description") or "")[:100]}, ensure_ascii=False))


async def add(http: httpx.AsyncClient, owner: str, voice_id: str, name: str) -> None:
    r = await http.post(f"/v1/voices/add/{owner}/{voice_id}", json={"new_name": name})
    print(r.status_code, r.text[:300])


async def amain(args) -> None:
    cfgmod.load_env_file()
    cfg = cfgmod.load(args.config)
    key = cfgmod.secret("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("Brak ELEVENLABS_API_KEY (wpisz go w /etc/jarvis/env).")
    async with httpx.AsyncClient(base_url=API, headers={"xi-api-key": key}, timeout=30) as http:
        if args.cmd == "add":
            await add(http, args.owner, args.voice_id, args.name)
            return
        out = Path(args.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        if args.cmd == "mine":
            await mine(http, out, args.max, cfg["elevenlabs"]["tts_model"])
        else:
            await polish(http, out, args.max)
        print(f"pliki w: {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, default_max in (("mine", 6), ("polish", 8)):
        s = sub.add_parser(name)
        s.add_argument("--out", default="/tmp/probki")
        s.add_argument("--max", type=int, default=default_max)
    a = sub.add_parser("add")
    a.add_argument("owner")
    a.add_argument("voice_id")
    a.add_argument("name")
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
