"""Narzędzia Jarvisa dla mózgu (Claude Code) jako serwer MCP po stdio.

Claude Code uruchamia go sam (--mcp-config). Prosty JSON-RPC linia po linii,
bez zależności: initialize, tools/list, tools/call, ping.

Narzędzia: pogoda, kalendarz Google (odczyt i dodawanie), projekty z GitHuba.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime, time as dtime, timedelta

import httpx

from . import config as cfgmod
from .skills import gcal, github, weather

log = logging.getLogger("jarvis.mcp")

TOOLS = [
    {
        "name": "weather",
        "description": "Current weather and daily forecast (Open-Meteo) for Jonasz's city, Gorzów Wielkopolski. "
                       "Use for any question about weather, temperature, rain, wind, sunrise or sunset.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Number of forecast days, 1-16 (today = 1).", "default": 3},
        }},
    },
    {
        "name": "calendar_events",
        "description": "List events from Jonasz's Google Calendar between two dates (inclusive). "
                       "Use for the daily plan, 'co mam jutro', free time, upcoming events.",
        "inputSchema": {"type": "object", "properties": {
            "start_date": {"type": "string", "description": "YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD, defaults to start_date"},
        }, "required": ["start_date"]},
    },
    {
        "name": "calendar_add",
        "description": "Add an event to Jonasz's Google Calendar. ONLY call this after you have read the event back "
                       "(title, day, time) and Jonasz explicitly confirmed it in his last message.",
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "Local time YYYY-MM-DDTHH:MM, or YYYY-MM-DD for an all-day event"},
            "end": {"type": "string", "description": "Optional, same format as start"},
            "duration_minutes": {"type": "integer", "description": "Optional, used when end is not given (default 60)"},
            "description": {"type": "string"},
            "location": {"type": "string"},
        }, "required": ["title", "start"]},
    },
    {
        "name": "github_projects",
        "description": "Jonasz's GitHub projects (account from config): repositories with recent activity.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "Activity window in days (default 14). Use a large number for all repos."},
            "limit": {"type": "integer", "description": "Max repositories (default 5)"},
        }},
    },
    {
        "name": "github_repo",
        "description": "Details of one of Jonasz's GitHub repositories: description, last commits, README start.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
]


class Tools:
    def __init__(self, cfg: dict, client: httpx.AsyncClient):
        self.cfg = cfg
        s = cfg["skills"]
        self.w = s["weather"]
        self.gh_cfg = s["github"]
        self.gh = github.GitHub(client, s["github"]["user"], cfgmod.secret("GITHUB_TOKEN"))
        self.cal = gcal.GoogleCalendar(client, s["calendar"]["credentials"], s["calendar"]["calendar_id"])
        self.client = client

    async def call(self, name: str, args: dict) -> str:
        if name == "weather":
            data = await weather.fetch(self.client, self.w["lat"], self.w["lon"], int(args.get("days") or 3))
            return weather.summary(data, self.w["place"])
        if name == "calendar_events":
            start = date.fromisoformat(args["start_date"])
            end = date.fromisoformat(args.get("end_date") or args["start_date"])
            evs = await self.cal.events(datetime.combine(start, dtime.min, gcal.TZ),
                                        datetime.combine(end + timedelta(days=1), dtime.min, gcal.TZ))
            return gcal.events_summary(evs)
        if name == "calendar_add":
            start = gcal.parse_when(args["start"])
            end = gcal.parse_when(args["end"]) if args.get("end") else None
            if end is None and isinstance(start, datetime) and args.get("duration_minutes"):
                end = start + timedelta(minutes=int(args["duration_minutes"]))
            ev = await self.cal.add(args["title"], start, end, args.get("description"), args.get("location"))
            return "Dodano: " + gcal.events_summary([ev])
        if name == "github_projects":
            repos = await self.gh.projects(int(args.get("days") or self.gh_cfg["days"]),
                                           int(args.get("limit") or self.gh_cfg["top"]), self.gh_cfg["pinned"])
            return github.projects_summary(repos)
        if name == "github_repo":
            return github.details_summary(await self.gh.repo_details(args["name"]))
        raise ValueError(f"nieznane narzędzie {name}")


async def serve(cfg: dict) -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=4 * 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    out = sys.stdout

    def send(obj: dict) -> None:
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    async with httpx.AsyncClient() as client:
        tools = Tools(cfg, client)
        while line := await reader.readline():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid, method = msg.get("id"), msg.get("method")
            if mid is None:  # powiadomienie, np. notifications/initialized
                continue
            params = msg.get("params") or {}
            if method == "initialize":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "jarvis", "version": "0.1"},
                }})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                result = await call_tool(tools, params.get("name", ""), params.get("arguments") or {})
                send({"jsonrpc": "2.0", "id": mid, "result": result})
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"brak metody {method}"}})


async def call_tool(tools: Tools, name: str, args: dict) -> dict:
    try:
        text = await tools.call(name, args)
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except gcal.CalendarNotConfigured:
        text = "Kalendarz Google nie jest jeszcze połączony z Jarvisem."
    except httpx.HTTPStatusError as exc:
        text = f"Błąd usługi: HTTP {exc.response.status_code}"
    except Exception as exc:  # błąd narzędzia nie może zabić serwera
        log.exception("narzędzie %s", name)
        text = f"Błąd: {type(exc).__name__}: {exc}"
    return {"content": [{"type": "text", "text": text}], "isError": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    cfgmod.load_env_file()
    asyncio.run(serve(cfgmod.load(args.config)))


if __name__ == "__main__":
    main()
