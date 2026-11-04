"""Projekty z GitHuba (konto Jonaszq): aktywne repozytoria i szczegóły jednego."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import httpx

API = "https://api.github.com"


class GitHub:
    def __init__(self, client: httpx.AsyncClient, user: str, token: str | None = None):
        self.client = client
        self.user = user
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "jarvis-voice-assistant"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.token = token

    async def _get(self, path: str, **params):
        r = await self.client.get(API + path, headers=self.headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    async def repos(self) -> list[dict]:
        if self.token:  # z tokenem widać też prywatne
            data = await self._get("/user/repos", affiliation="owner", sort="pushed", per_page=100)
        else:
            data = await self._get(f"/users/{self.user}/repos", sort="pushed", per_page=100)
        return [r for r in data if isinstance(r, dict)]

    async def projects(self, days: int = 14, top: int = 5, pinned: list[str] | None = None) -> list[dict]:
        """Repozytoria z pushem w ostatnich `days` dniach (max `top`), plus przypięte."""
        repos = await self.repos()
        since = datetime.now(timezone.utc) - timedelta(days=days)
        pinned_l = [p.lower() for p in (pinned or [])]
        out = [r for r in repos if r["name"].lower() in pinned_l]
        recent = [
            r for r in repos
            if r not in out and not r.get("archived") and r.get("pushed_at")
            and datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00")) >= since
        ]
        out += recent[:top]
        return out

    async def repo_details(self, name: str) -> dict:
        repo = await self._get(f"/repos/{self.user}/{name}")
        commits = await self._get(f"/repos/{self.user}/{name}/commits", per_page=5)
        readme = ""
        try:
            data = await self._get(f"/repos/{self.user}/{name}/readme")
            readme = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
        except httpx.HTTPStatusError:
            pass
        return {"repo": repo, "commits": commits, "readme": readme}


async def _tree(gh: "GitHub", name: str) -> list[str]:
    repo = await gh._get(f"/repos/{gh.user}/{name}")
    data = await gh._get(f"/repos/{gh.user}/{name}/git/trees/{repo['default_branch']}", recursive="1")
    return [t["path"] for t in data.get("tree", []) if t.get("type") == "blob"]


async def _file(gh: "GitHub", name: str, path: str) -> str:
    data = await gh._get(f"/repos/{gh.user}/{name}/contents/{path}")
    if isinstance(data, list):
        return "To katalog: " + ", ".join(d["name"] for d in data)
    return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")


GitHub.tree = _tree
GitHub.file = _file


def ago(iso: str | None) -> str:
    if not iso:
        return "?"
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if delta.days >= 1:
        return f"{delta.days} dni temu"
    hours = delta.seconds // 3600
    return f"{hours} godz. temu" if hours else "przed chwilą"


def projects_summary(repos: list[dict]) -> str:
    if not repos:
        return "Brak repozytoriów z ostatnimi zmianami."
    return "\n".join(
        f"- {r['name']}{' (prywatne)' if r.get('private') else ''}: {r.get('description') or 'bez opisu'}; "
        f"język {r.get('language') or '?'}; ostatni push {ago(r.get('pushed_at'))}"
        for r in repos
    )


def details_summary(d: dict) -> str:
    r = d["repo"]
    lines = [
        f"{r['name']}: {r.get('description') or 'bez opisu'}",
        f"język {r.get('language') or '?'}, gwiazdki {r.get('stargazers_count', 0)}, "
        f"otwarte zgłoszenia {r.get('open_issues_count', 0)}, domyślna gałąź {r.get('default_branch')}, "
        f"ostatni push {ago(r.get('pushed_at'))}",
        "Ostatnie commity:",
    ]
    for c in d["commits"][:5]:
        msg = (c.get("commit", {}).get("message") or "").splitlines()[0][:120]
        when = c.get("commit", {}).get("author", {}).get("date")
        lines.append(f"- {msg} ({ago(when)})")
    if d["readme"]:
        lines.append("README (początek):\n" + d["readme"][:1500])
    return "\n".join(lines)
