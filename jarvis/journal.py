"""Dziennik Jonasza: nastrój, plany, co zrobione. Lokalnie na Pi, nigdy w repo.

Jeden plik na dzień: <dir>/RRRR-MM-DD.md
    # Dziennik 2026-11-04 (środa)
    - 19:40 [nastrój] zmęczony po szkole
    - 19:41 [zrobione] skończył Jarvisa etap 4a
    ## Podsumowanie dnia
    ...

Wpisy dodaje mózg (narzędzie journal_add), "zapomnij to" usuwa ostatni
wpis od razu (komenda lokalna), a o 23:00 osobne wywołanie Claude pisze
podsumowanie dnia.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger("jarvis.journal")

TZ = ZoneInfo("Europe/Warsaw")
DAYS = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
KINDS = {"mood": "nastrój", "plan": "plan", "done": "zrobione", "note": "notatka"}
SUMMARY_HEADER = "## Podsumowanie dnia"
_ENTRY = re.compile(r"^- \d\d:\d\d \[")


class Journal:
    def __init__(self, directory: str | Path, clock: Callable[[], datetime] = lambda: datetime.now(TZ)):
        self.dir = Path(directory).expanduser()
        self.clock = clock

    def path(self, day: date) -> Path:
        return self.dir / f"{day.isoformat()}.md"

    def read(self, day: date) -> str:
        p = self.path(day)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def entries(self, day: date) -> list[str]:
        return [line for line in self.read(day).splitlines() if _ENTRY.match(line)]

    def add(self, text: str, kind: str = "note") -> str:
        now = self.clock()
        text = " ".join(text.split())
        label = KINDS.get(kind, kind)
        line = f"- {now:%H:%M} [{label}] {text}"
        p = self.path(now.date())
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        content = self.read(now.date()) or f"# Dziennik {now.date().isoformat()} ({DAYS[now.weekday()]})\n\n"
        if SUMMARY_HEADER in content:  # wpis po podsumowaniu: przed nim, żeby plik był czytelny
            head, tail = content.split(SUMMARY_HEADER, 1)
            content = head.rstrip("\n") + "\n" + line + "\n\n" + SUMMARY_HEADER + tail
        else:
            content = content.rstrip("\n") + "\n" + line + "\n"
        p.write_text(content, encoding="utf-8")
        return line

    def forget_last(self) -> str | None:
        """Usuwa ostatni dzisiejszy wpis. Zwraca jego treść albo None."""
        today = self.clock().date()
        lines = self.read(today).splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if _ENTRY.match(lines[i]):
                removed = lines.pop(i)
                self.path(today).write_text("\n".join(lines) + "\n", encoding="utf-8")
                return removed.split("] ", 1)[-1]
        return None

    def has_summary(self, day: date) -> bool:
        return SUMMARY_HEADER in self.read(day)

    def write_summary(self, day: date, summary: str) -> None:
        content = self.read(day)
        if SUMMARY_HEADER in content:
            content = content.split(SUMMARY_HEADER, 1)[0].rstrip("\n") + "\n"
        content = content.rstrip("\n") + f"\n\n{SUMMARY_HEADER}\n\n{summary.strip()}\n"
        self.path(day).write_text(content, encoding="utf-8")

    def summary(self, day: date) -> str:
        content = self.read(day)
        return content.split(SUMMARY_HEADER, 1)[1].strip() if SUMMARY_HEADER in content else ""

    def range_text(self, start: date, end: date) -> str:
        out, d = [], start
        while d <= end:
            if txt := self.read(d).strip():
                out.append(txt)
            d += timedelta(days=1)
        return "\n\n".join(out) or "Brak wpisów w tym okresie."


SUMMARY_PROMPT = """You write the evening summary of Jonasz's personal journal. Write in Polish, 3-6 short sentences, \
second person ("Dziś ..."), warm but matter-of-fact: how his mood went, what he got done, what he planned and \
what is still open for tomorrow, plus anything notable from his calendar. Use only the facts given. \
Plain text, no headings, no lists, no markdown."""


async def summarize_day(journal: Journal, day: date, claude_bin: str, model: str, workdir: Path,
                        calendar_text: str = "") -> str | None:
    """Jednorazowe wywołanie claude -p (subskrypcja). Zwraca podsumowanie albo None, gdy dzień jest pusty."""
    entries = journal.entries(day)
    if not entries:
        return None
    material = f"Data: {day.isoformat()} ({DAYS[day.weekday()]})\n\nWpisy z dziennika:\n" + "\n".join(entries)
    if calendar_text:
        material += f"\n\nKalendarz tego dnia:\n{calendar_text}"
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    workdir.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        claude_bin, "-p", "--model", model, "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
        "--no-session-persistence", "--system-prompt", SUMMARY_PROMPT,
        cwd=str(workdir), env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(material.encode("utf-8")), timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p: kod {proc.returncode}: {err.decode(errors='replace')[:300]}")
    text = out.decode("utf-8").strip()
    journal.write_summary(day, text)
    return text


async def summary_loop(journal: Journal, at: str, run: Callable[[date], "asyncio.Future"],
                       clock: Callable[[], datetime] = lambda: datetime.now(TZ)) -> None:
    """Codziennie o `at` (HH:MM) podsumowuje dzień. Po starcie nadrabia wczoraj, jeśli Pi było wyłączone."""
    hh, mm = (int(x) for x in at.split(":"))
    yesterday = clock().date() - timedelta(days=1)
    if journal.entries(yesterday) and not journal.has_summary(yesterday):
        await _safe(run, yesterday)
    while True:
        now = clock()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        await _safe(run, target.date())


async def _safe(run, day: date) -> None:
    try:
        text = await run(day)
        log.info("podsumowanie dnia %s: %s", day, "zapisane" if text else "brak wpisów")
    except Exception:
        log.exception("podsumowanie dnia %s nieudane", day)
