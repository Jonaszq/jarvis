#!/usr/bin/env python3
"""Podsumowanie prawdziwych rozmów z logs/turns.jsonl: mediana i p90 każdego kroku.

  python scripts/bench.py                 wszystkie tury
  python scripts/bench.py --last 20       ostatnie 20
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

STEPS = ["transcript", "first_token", "first_sentence", "first_audio", "brain_done", "done"]
LABELS = {
    "transcript": "tekst z STT",
    "first_token": "pierwszy token",
    "first_sentence": "pierwsze zdanie",
    "first_audio": "PIERWSZE SŁOWO",
    "brain_done": "mózg skończył",
    "done": "koniec mówienia",
}


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(Path.home() / "jarvis/logs/turns.jsonl"))
    ap.add_argument("--last", type=int, default=0)
    args = ap.parse_args()
    lines = [json.loads(l) for l in Path(args.path).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.last:
        lines = lines[-args.last:]
    ok = [r for r in lines if "error" not in r and not r.get("empty")]
    print(f"tury: {len(lines)} (udane {len(ok)}, błędy {sum('error' in r for r in lines)}, "
          f"puste {sum(bool(r.get('empty')) for r in lines)})")
    print(f"{'krok (od końca wypowiedzi)':28} {'mediana':>8} {'p90':>8} {'n':>4}")
    for step in STEPS:
        vals = [r["since_speech_end"][step] for r in ok if step in r.get("since_speech_end", {})]
        if vals:
            print(f"{LABELS[step]:28} {statistics.median(vals):8.2f} {pct(vals, 0.9):8.2f} {len(vals):4}")
    for r in lines[-3:]:
        print(f"- {r.get('heard', '')!r} -> {str(r.get('answer', r.get('error', '')))[:70]!r}")


if __name__ == "__main__":
    main()
