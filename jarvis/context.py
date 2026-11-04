"""Kontekst dnia doklejany do każdego pytania: data i godzina, pogoda,
kalendarz na dziś i jutro, aktywne projekty z GitHuba.

Odświeżany w tle (domyślnie co 5 min), więc pytanie nie czeka na sieć.
Każdy kawałek zawodzi osobno: brak kalendarza nie psuje pogody.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime, timedelta
from typing import Callable

import httpx

from . import config as cfgmod
from .skills import gcal, github, weather

log = logging.getLogger("jarvis.context")

MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia",
          "września", "października", "listopada", "grudnia"]


def now_line(now: datetime) -> str:
    return (f"Teraz: {gcal.DAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year}, "
            f"godzina {now:%H:%M} (data ISO {now.date().isoformat()}).")


class DayContext:
    def __init__(self, cfg: dict, clock: Callable[[], datetime] = lambda: datetime.now(gcal.TZ)):
        self.cfg = cfg
        self.clock = clock
        self.parts: dict[str, str] = {}
        self._task: asyncio.Task | None = None

    def text(self) -> str:
        lines = [now_line(self.clock())]
        for key, title in (("weather", "Pogoda"), ("calendar", "Kalendarz (dziś i jutro)"), ("github", "Aktywne projekty GitHub")):
            if self.parts.get(key):
                lines.append(f"{title}:\n{self.parts[key]}")
        return "\n\n".join(lines)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        every = self.cfg["skills"].get("context_refresh_s", 300)
        async with httpx.AsyncClient() as client:
            while True:
                await self.refresh(client)
                await asyncio.sleep(every)

    async def refresh(self, client: httpx.AsyncClient) -> None:
        s = self.cfg["skills"]
        await asyncio.gather(self._weather(client, s), self._calendar(client, s), self._github(client, s))

    async def _weather(self, client, s) -> None:
        try:
            w = s["weather"]
            self.parts["weather"] = weather.summary(await weather.fetch(client, w["lat"], w["lon"], 2), w["place"])
        except Exception as exc:
            log.warning("pogoda: %s", exc)

    async def _calendar(self, client, s) -> None:
        cal = gcal.GoogleCalendar(client, s["calendar"]["credentials"], s["calendar"]["calendar_id"])
        if not cal.configured:
            self.parts["calendar"] = "Kalendarz Google nie jest jeszcze połączony."
            return
        try:
            today = self.clock().date()
            start = datetime.combine(today, dtime.min, gcal.TZ)
            self.parts["calendar"] = gcal.events_summary(await cal.events(start, start + timedelta(days=2)))
        except Exception as exc:
            log.warning("kalendarz: %s", exc)

    async def _github(self, client, s) -> None:
        g = s["github"]
        try:
            gh = github.GitHub(client, g["user"], cfgmod.secret("GITHUB_TOKEN"))
            self.parts["github"] = github.projects_summary(await gh.projects(g["days"], g["top"], g["pinned"]))
        except Exception as exc:
            log.warning("github: %s", exc)
