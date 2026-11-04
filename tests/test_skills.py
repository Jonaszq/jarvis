import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

from jarvis import config as cfgmod
from jarvis.context import DayContext
from jarvis.mcp_server import Tools, call_tool
from jarvis.skills import gcal, github, weather

WEATHER = {
    "current": {"temperature_2m": 7.1, "apparent_temperature": 4.0, "weather_code": 61, "wind_speed_10m": 12},
    "daily": {"time": ["2026-11-04"], "weather_code": [3], "temperature_2m_min": [2.0], "temperature_2m_max": [8.5],
              "precipitation_probability_max": [70], "precipitation_sum": [3.2], "wind_speed_10m_max": [25],
              "sunrise": ["2026-11-04T06:52"], "sunset": ["2026-11-04T16:15"]},
}


def iso_days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat().replace("+00:00", "Z")


REPOS = [
    {"name": "jarvis", "pushed_at": iso_days_ago(0), "description": "asystent", "language": "Python"},
    {"name": "stary", "pushed_at": iso_days_ago(60), "description": None, "language": "Java"},
    {"name": "szkola", "pushed_at": iso_days_ago(3), "description": "INF04", "language": "Java"},
]


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "open-meteo" in url:
        return httpx.Response(200, json=WEATHER)
    if "users/Jonaszq/repos" in url:
        return httpx.Response(200, json=REPOS)
    if "oauth2.googleapis.com/token" in url:
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    if "calendar/v3" in url and request.method == "GET":
        return httpx.Response(200, json={"items": [
            {"summary": "Dentysta", "start": {"dateTime": "2026-11-05T10:00:00+01:00"}, "end": {"dateTime": "2026-11-05T11:00:00+01:00"}},
            {"summary": "Urodziny Ani", "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}},
        ]})
    if "calendar/v3" in url and request.method == "POST":
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, json={**body, "id": "x"})
    return httpx.Response(404)


def cfg_with(tmp_path, creds=True):
    cfg = cfgmod.load(tmp_path / "brak.yaml")
    path = tmp_path / "google.json"
    if creds:
        path.write_text(json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    cfg["skills"]["calendar"]["credentials"] = str(path)
    return cfg


def test_weather_summary_in_polish():
    text = weather.summary(WEATHER)
    assert "7.1°C" in text and "słaby deszcz" in text and "szansa opadów 70%" in text and "zachód 16:15" in text


async def test_github_projects_recent_and_pinned():
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        gh = github.GitHub(c, "Jonaszq")
        names = [r["name"] for r in await gh.projects(14, 5)]
        assert names == ["jarvis", "szkola"]
        names = [r["name"] for r in await gh.projects(14, 1, pinned=["stary"])]
        assert names == ["stary", "jarvis"]


def test_parse_when_and_events_summary():
    assert gcal.parse_when("2026-11-05") == date(2026, 11, 5)
    dt = gcal.parse_when("2026-11-05T15:30")
    assert dt.hour == 15 and dt.tzinfo == gcal.TZ
    text = gcal.events_summary([
        {"summary": "Dentysta", "start": {"dateTime": "2026-11-05T09:00:00Z"}, "end": {"dateTime": "2026-11-05T10:00:00Z"}},
    ])
    assert "czwartek 2026-11-05" in text and "10:00–11:00 Dentysta" in text  # UTC -> czas polski


async def test_tools_calendar_add_and_list(tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        tools = Tools(cfg_with(tmp_path), c)
        out = await call_tool(tools, "calendar_add", {"title": "Fryzjer", "start": "2026-11-06T15:00", "duration_minutes": 30})
        assert not out["isError"] and "15:00–15:30 Fryzjer" in out["content"][0]["text"]
        out = await call_tool(tools, "calendar_events", {"start_date": "2026-11-05"})
        assert "Dentysta" in out["content"][0]["text"] and "cały dzień Urodziny Ani" in out["content"][0]["text"]
        out = await call_tool(tools, "weather", {"days": 1})
        assert "Gorzów" in out["content"][0]["text"]


async def test_calendar_not_connected_is_friendly(tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await call_tool(Tools(cfg_with(tmp_path, creds=False), c), "calendar_events", {"start_date": "2026-11-05"})
        assert out["isError"] and "nie jest jeszcze połączony" in out["content"][0]["text"]


async def test_day_context(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    fixed = datetime(2026, 11, 4, 19, 5, tzinfo=gcal.TZ)
    ctx = DayContext(cfg_with(tmp_path), clock=lambda: fixed)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await ctx.refresh(c)
    text = ctx.text()
    assert "środa, 4 listopada 2026, godzina 19:05" in text
    assert "Dentysta" in text and "jarvis" in text and "7.1°C" in text


def test_mcp_server_handshake(tmp_path):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "nieznana"},
    ]
    p = subprocess.run([sys.executable, "-m", "jarvis.mcp_server", "--config", str(tmp_path / "x.yaml")],
                       input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True, timeout=20)
    replies = [json.loads(line) for line in p.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert names == {"weather", "calendar_events", "calendar_add", "github_projects", "github_repo"}
    assert "error" in replies[2]


def test_brain_command_has_mcp(tmp_path):
    from jarvis.brain.claude_code import build_command

    cmd = build_command("claude", "haiku", None, tmp_path / "p.md", web=True, mcp_config=tmp_path / "m.json")
    assert cmd[cmd.index("--mcp-config") + 1].endswith("m.json")
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch,mcp__jarvis"
    assert "--strict-mcp-config" in cmd
