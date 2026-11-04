"""Ile Jarvis zużył limitu: Claude (subskrypcja) i ElevenLabs (kredyty).

Claude: CLI po każdej odpowiedzi podaje zużycie (tokeny i równowartość w $
według cennika API). Na subskrypcji nic nie płacisz, ale ta kwota dobrze
pokazuje, ile limitu sesji (okno 5 godzin) zjadł Jarvis. Zapisujemy to do
logs/usage.jsonl i sumujemy z ostatnich 5 godzin.

Widełki (config usage.claude_bands, w $ równowartości na 5 godzin):
    poniżej 0,5 = mało, 0,5-2 = średnio, powyżej 2 = dużo
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Warsaw")
MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia",
          "września", "października", "listopada", "grudnia"]


class UsageLog:
    def __init__(self, path: str | Path, clock=time.time):
        self.path = Path(path).expanduser()
        self.clock = clock

    def add_result(self, event: dict) -> None:
        """Zdarzenie "result" z Claude Code (stream-json)."""
        u = event.get("usage") or {}
        rec = {
            "t": round(self.clock(), 1),
            "cost": float(event.get("total_cost_usd") or 0.0),
            "in": int(u.get("input_tokens") or 0),
            "cache_w": int(u.get("cache_creation_input_tokens") or 0),
            "cache_r": int(u.get("cache_read_input_tokens") or 0),
            "out": int(u.get("output_tokens") or 0),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def window(self, hours: float = 5.0) -> dict:
        since = self.clock() - hours * 3600
        n, cost, tokens = 0, 0.0, 0
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("t", 0) >= since:
                n += 1
                cost += r.get("cost", 0.0)
                tokens += r.get("in", 0) + r.get("cache_w", 0) + r.get("cache_r", 0) + r.get("out", 0)
        return {"answers": n, "cost": cost, "tokens": tokens}


def band(value: float, limits: tuple[float, float], names: tuple[str, str, str]) -> str:
    lo, hi = limits
    return names[0] if value < lo else names[1] if value < hi else names[2]


def _num(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def report(log: UsageLog, claude_bands=(0.5, 2.0), hours: float = 5.0) -> str:
    w = log.window(hours)
    level = band(w["cost"], claude_bands, ("małe", "średnie", "duże"))
    return (f"W ostatnich {int(hours)} godzinach odpowiedziałem {w['answers']} razy. Zużycie limitu Claude: {level}, "
            f"około {w['tokens'] // 1000} tysięcy tokenów. Dokładny procent jest w ustawieniach Claude, w zakładce użycie.")
