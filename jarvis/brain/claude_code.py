"""Mózg: jeden ciepły proces Claude Code CLI na subskrypcji konta.

Zamiast uruchamiać `claude -p` od zera przy każdym pytaniu (to było ~12 s),
trzymamy jeden proces w trybie stream-json. Pytania wpisujemy na stdin jako
linie JSON, a odpowiedź czytamy ze stdout kawałek po kawałku.

Reset rozmowy = zamknięcie procesu i start nowego (z --no-session-persistence
nic nie zostaje na dysku). Nowy proces startuje od razu w tle, żeby kolejne
pytanie nie czekało na zimny start.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncIterator, Callable

log = logging.getLogger("jarvis.brain")


def build_command(
    claude_bin: str,
    model: str,
    effort: str | None,
    prompt_file: Path,
) -> list[str]:
    cmd = [
        claude_bin,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", model,
        "--system-prompt-file", str(prompt_file),
        "--tools", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def user_message(text: str) -> bytes:
    msg = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


class BrainError(RuntimeError):
    pass


class ClaudeCodeBrain:
    def __init__(
        self,
        command: list[str],
        workdir: Path,
        timeout_s: float = 30.0,
        warmup: bool = False,
        context: Callable[[], str] | None = None,
    ):
        self.command = command
        self.workdir = Path(workdir).expanduser()
        self.timeout_s = timeout_s
        self.warmup = warmup
        self.context = context  # etap 4a: "kontekst dnia" doklejany do pytania
        self._proc: asyncio.subprocess.Process | None = None
        self._spawning: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # --- cykl życia procesu ---------------------------------------------

    async def start(self) -> None:
        await self._ensure()
        if self.warmup:
            # Jedno krótkie pytanie na starcie: ładuje model po stronie serwera
            # i sprawdza logowanie. Kosztuje odrobinę limitu subskrypcji.
            async for _ in self.ask("Reply with: ok"):
                pass
            await self.reset()

    async def reset(self) -> None:
        await self._kill()
        self._spawning = asyncio.create_task(self._spawn())

    async def close(self) -> None:
        if self._spawning:
            try:
                await self._spawning
            except Exception:
                pass
        await self._kill()

    async def _ensure(self) -> asyncio.subprocess.Process:
        if self._spawning is not None:
            task, self._spawning = self._spawning, None
            await task
        if self._proc is None or self._proc.returncode is not None:
            await self._spawn()
        assert self._proc is not None
        return self._proc

    async def _spawn(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        # Klucz API w środowisku przełączyłby CLI na płatne API zamiast subskrypcji.
        env.pop("ANTHROPIC_API_KEY", None)
        log.info("start procesu mózgu: %s", " ".join(self.command))
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=str(self.workdir),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=4 * 1024 * 1024,
        )
        self._stderr_task = asyncio.create_task(self._log_stderr(self._proc))

    async def _log_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        async for line in proc.stderr:
            log.warning("claude stderr: %s", line.decode(errors="replace").rstrip())

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=2)
        except (asyncio.TimeoutError, ProcessLookupError):
            proc.kill()
            await proc.wait()
        if self._stderr_task:
            self._stderr_task.cancel()

    # --- pytanie ----------------------------------------------------------

    async def ask(self, text: str) -> AsyncIterator[str]:
        async with self._lock:
            proc = await self._ensure()
            if self.context:
                ctx = self.context().strip()
                if ctx:
                    text = f"<context>\n{ctx}\n</context>\n\n{text}"
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(user_message(text))
            await proc.stdin.drain()

            streamed = False
            try:
                while True:
                    line = await asyncio.wait_for(proc.stdout.readline(), self.timeout_s)
                    if not line:
                        raise BrainError(f"proces mózgu się zakończył (kod {proc.returncode})")
                    event = _parse(line)
                    if event is None:
                        continue
                    kind = event.get("type")
                    if kind == "stream_event":
                        delta = _text_delta(event)
                        if delta:
                            streamed = True
                            yield delta
                    elif kind == "assistant" and not streamed:
                        # Bez strumienia tokenów (starsza wersja CLI): cała wiadomość naraz.
                        full = _assistant_text(event)
                        if full:
                            streamed = True
                            yield full
                    elif kind == "result":
                        if event.get("is_error") or event.get("subtype") not in (None, "success"):
                            raise BrainError(f"błąd mózgu: {event.get('result') or event.get('subtype')}")
                        if not streamed and event.get("result"):
                            yield str(event["result"])
                        return
            except BaseException:
                # Proces w nieznanym stanie (timeout, przerwanie): następne pytanie dostanie nowy.
                await self._kill()
                raise


def _parse(line: bytes) -> dict | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        log.debug("nie-JSON ze stdout: %r", line[:200])
        return None
    return event if isinstance(event, dict) else None


def _text_delta(event: dict) -> str | None:
    inner = event.get("event") or {}
    if inner.get("type") != "content_block_delta":
        return None
    delta = inner.get("delta") or {}
    if delta.get("type") == "text_delta":
        return delta.get("text") or None
    return None


def _assistant_text(event: dict) -> str:
    content = (event.get("message") or {}).get("content") or []
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
