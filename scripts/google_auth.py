#!/usr/bin/env python3
"""Jednorazowe połączenie Jarvisa z Kalendarzem Google (OAuth, przeglądarka).

Na Pi, przez tunel z komputera (Pi nie ma przeglądarki):
  1. Na komputerze:  ssh -L 8765:localhost:8765 jonasz@pi-server.local
  2. W tej sesji:    cd ~/jarvis && .venv/bin/python scripts/google_auth.py ~/client_secret.json
  3. Otwórz wypisany link w przeglądarce na komputerze, zaloguj się i zezwól.

Wynik: ~/.config/jarvis/google.json (chmod 600) z refresh tokenem.
Plik client_secret.json z Google Cloud można potem usunąć.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import urllib.parse
from pathlib import Path

import httpx

PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
OUT = Path("~/.config/jarvis/google.json").expanduser()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    raw = json.loads(Path(sys.argv[1]).expanduser().read_text())
    client = raw.get("installed") or raw.get("web") or raw
    cid, csecret = client["client_id"], client["client_secret"]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") != state or "code" not in q:
                self.send_response(400)
                self.end_headers()
                self.wfile.write("Błąd: brak kodu albo zły state.".encode())
                result["error"] = q.get("error", "brak kodu")
                return
            result["code"] = q["code"]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Gotowe. Jarvis ma dostęp do kalendarza, możesz zamknąć kartę.</h2>".encode())

        def log_message(self, *a):
            pass

    print("\nOtwórz ten link w przeglądarce na komputerze (tunel ssh -L 8765 musi być otwarty):\n")
    print(url, "\n", flush=True)
    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as srv:
        while not result:
            srv.handle_request()
    if "code" not in result:
        sys.exit(f"Nie udało się: {result['error']}")
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": result["code"], "client_id": cid, "client_secret": csecret, "redirect_uri": REDIRECT,
        "grant_type": "authorization_code", "code_verifier": verifier,
    }, timeout=15)
    r.raise_for_status()
    tok = r.json()
    if "refresh_token" not in tok:
        sys.exit("Google nie dał refresh tokena. Usuń dostęp Jarvisa na myaccount.google.com/permissions i spróbuj jeszcze raz.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"client_id": cid, "client_secret": csecret, "refresh_token": tok["refresh_token"]}, f)
    # Sprawdzenie: lista kalendarzy nie wymaga dodatkowego zakresu, więc czytamy najbliższe wydarzenie.
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    ev = httpx.get("https://www.googleapis.com/calendar/v3/calendars/primary/events",
                   params={"maxResults": 1, "singleEvents": "true", "orderBy": "startTime",
                           "timeMin": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},
                   headers=h, timeout=15)
    print(f"Zapisano {OUT}. Test kalendarza: HTTP {ev.status_code}.")


if __name__ == "__main__":
    main()
