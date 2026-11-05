"""Pomiar czasów każdej rozmowy.

Każda rozmowa (tura) dopisuje jedną linię JSON do turns.jsonl. Czasy są
liczone od końca wypowiedzi użytkownika, bo to on wyznacza odczuwalne
opóźnienie ("ile czekam od zamilknięcia do pierwszego słowa").
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Kolejność kroków jednej tury. Nie każdy krok musi wystąpić.
MARKS = (
    "wake",            # wykryto wake word
    "speech_end",      # koniec wypowiedzi (VAD)
    "transcript",      # gotowy tekst z STT
    "first_token",     # pierwszy kawałek odpowiedzi z mózgu
    "first_sentence",  # pierwsze pełne zdanie gotowe do TTS
    "first_audio",     # pierwszy kawałek audio wysłany do głośnika
    "brain_done",      # mózg skończył odpowiadać
    "done",            # ostatnie audio zagrane
)


@dataclass
class Turn:
    clock: Callable[[], float] = time.monotonic
    started_at: float = field(default_factory=time.time)
    marks: dict[str, float] = field(default_factory=dict)
    info: dict[str, object] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        # Liczy się pierwsze wystąpienie (np. pierwszy token).
        if name not in self.marks:
            self.marks[name] = self.clock()

    def durations(self) -> dict[str, float]:
        """Czasy w sekundach względem końca wypowiedzi (albo wake worda)."""
        base = self.marks.get("speech_end", self.marks.get("wake"))
        if base is None:
            return {}
        return {
            name: round(self.marks[name] - base, 3)
            for name in MARKS
            if name in self.marks and name not in ("wake", "speech_end")
        }

    def to_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "ts": round(self.started_at, 3),
            "since_speech_end": self.durations(),
        }
        if "wake" in self.marks and "speech_end" in self.marks:
            record["speaking_s"] = round(self.marks["speech_end"] - self.marks["wake"], 3)
        record.update(self.info)
        return record


class MetricsLog:
    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser()

    def write(self, turn: Turn) -> dict[str, object]:
        record = turn.to_record()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record
