"""Google Calendar przez REST (httpx), bez ciężkich bibliotek Google.

Dane logowania (client_id, client_secret, refresh_token) leżą w
~/.config/jarvis/google.json (chmod 600), tworzy je scripts/google_auth.py.
Nigdy w repo.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Europe/Warsaw")
API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
DEFAULT_CREDS = "~/.config/jarvis/google.json"

DAYS = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


class CalendarNotConfigured(RuntimeError):
    pass


class GoogleCalendar:
    def __init__(self, client: httpx.AsyncClient, creds_path: str | Path = DEFAULT_CREDS, calendar_id: str = "primary"):
        self.client = client
        self.path = Path(creds_path).expanduser()
        self.calendar_id = calendar_id
        self._token: str | None = None
        self._expires = 0.0

    @property
    def configured(self) -> bool:
        return self.path.exists()

    async def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        if not self.configured:
            raise CalendarNotConfigured("kalendarz nie jest jeszcze połączony")
        creds = json.loads(self.path.read_text(encoding="utf-8"))
        r = await self.client.post(TOKEN_URL, data={
            "client_id": creds["client_id"], "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"], "grant_type": "refresh_token",
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._expires = time.time() + int(data.get("expires_in", 3600))
        return self._token

    async def _req(self, method: str, path: str, **kw):
        token = await self._access_token()
        r = await self.client.request(method, API + path, headers={"Authorization": f"Bearer {token}"},
                                      timeout=10, **kw)
        r.raise_for_status()
        return r.json()

    async def events(self, start: datetime, end: datetime, limit: int = 50) -> list[dict]:
        data = await self._req("GET", f"/calendars/{self.calendar_id}/events", params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
            "orderBy": "startTime", "maxResults": limit, "timeZone": "Europe/Warsaw",
        })
        return data.get("items", [])

    async def add(self, title: str, start: datetime | date, end: datetime | date | None = None,
                  description: str | None = None, location: str | None = None) -> dict:
        body: dict = {"summary": title}
        if isinstance(start, datetime):
            end = end or start + timedelta(hours=1)
            body["start"] = {"dateTime": start.isoformat(), "timeZone": "Europe/Warsaw"}
            body["end"] = {"dateTime": end.isoformat(), "timeZone": "Europe/Warsaw"}
        else:  # cały dzień: koniec jest wyłączny
            end = end or start + timedelta(days=1)
            body["start"] = {"date": start.isoformat()}
            body["end"] = {"date": end.isoformat()}
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        return await self._req("POST", f"/calendars/{self.calendar_id}/events", json=body)


def parse_when(value: str) -> datetime | date:
    """'2026-11-05T15:00' (czas lokalny) albo '2026-11-05' (cały dzień)."""
    value = value.strip()
    if "T" not in value and " " not in value:
        return date.fromisoformat(value)
    dt = datetime.fromisoformat(value.replace(" ", "T"))
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def _start(ev: dict) -> tuple[str, str | None]:
    s = ev.get("start", {})
    if "dateTime" in s:
        dt = datetime.fromisoformat(s["dateTime"]).astimezone(TZ)
        return dt.date().isoformat(), dt.strftime("%H:%M")
    return s.get("date", "?"), None


def _end_time(ev: dict) -> str | None:
    e = ev.get("end", {})
    if "dateTime" in e:
        return datetime.fromisoformat(e["dateTime"]).astimezone(TZ).strftime("%H:%M")
    return None


def events_summary(events: list[dict]) -> str:
    if not events:
        return "brak wydarzeń"
    lines, last_day = [], None
    for ev in events:
        day, t = _start(ev)
        if day != last_day:
            d = date.fromisoformat(day) if day != "?" else None
            lines.append(f"{DAYS[d.weekday()]} {day}:" if d else f"{day}:")
            last_day = day
        when = f"{t}–{_end_time(ev)}" if t else "cały dzień"
        extra = f" ({ev['location']})" if ev.get("location") else ""
        lines.append(f"- {when} {ev.get('summary') or '(bez tytułu)'}{extra}")
    return "\n".join(lines)
