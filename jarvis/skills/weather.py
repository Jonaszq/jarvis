"""Pogoda z Open-Meteo (bez klucza). Domyślnie Gorzów Wielkopolski."""

from __future__ import annotations

import httpx

URL = "https://api.open-meteo.com/v1/forecast"

# Kody WMO -> opis po polsku
CODES = {
    0: "bezchmurnie", 1: "przeważnie pogodnie", 2: "częściowe zachmurzenie", 3: "pochmurno",
    45: "mgła", 48: "mgła z szadzią", 51: "lekka mżawka", 53: "mżawka", 55: "gęsta mżawka",
    56: "marznąca mżawka", 57: "marznąca mżawka", 61: "słaby deszcz", 63: "deszcz", 65: "ulewa",
    66: "marznący deszcz", 67: "marznący deszcz", 71: "słaby śnieg", 73: "śnieg", 75: "duży śnieg",
    77: "krupa śnieżna", 80: "przelotny deszcz", 81: "przelotne opady", 82: "gwałtowne ulewy",
    85: "przelotny śnieg", 86: "intensywny śnieg", 95: "burza", 96: "burza z gradem", 99: "burza z gradem",
}


def describe_code(code) -> str:
    return CODES.get(int(code) if code is not None else -1, "pogoda nieznana")


async def fetch(client: httpx.AsyncClient, lat: float, lon: float, days: int = 3) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "timezone": "Europe/Warsaw", "forecast_days": max(1, min(days, 16)),
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,sunrise,sunset,wind_speed_10m_max",
        "wind_speed_unit": "kmh",
    }
    r = await client.get(URL, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def summary(data: dict, place: str = "Gorzów Wielkopolski") -> str:
    lines = []
    cur = data.get("current") or {}
    if cur:
        lines.append(
            f"{place} teraz: {cur.get('temperature_2m')}°C (odczuwalna {cur.get('apparent_temperature')}°C), "
            f"{describe_code(cur.get('weather_code'))}, wiatr {cur.get('wind_speed_10m')} km/h"
        )
    d = data.get("daily") or {}
    for i, day in enumerate(d.get("time") or []):
        lines.append(
            f"{day}: {describe_code(d['weather_code'][i])}, {d['temperature_2m_min'][i]}–{d['temperature_2m_max'][i]}°C, "
            f"szansa opadów {d['precipitation_probability_max'][i]}%, opady {d['precipitation_sum'][i]} mm, "
            f"wiatr do {d['wind_speed_10m_max'][i]} km/h, wschód {d['sunrise'][i][-5:]}, zachód {d['sunset'][i][-5:]}"
        )
    return "\n".join(lines)
