import sys
from pathlib import Path

import pytest

from jarvis.brain.claude_code import BrainError, ClaudeCodeBrain, build_command

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def make(tmp_path, **kw):
    return ClaudeCodeBrain(FAKE, tmp_path / "work", **kw)


async def collect(brain, text):
    return "".join([c async for c in brain.ask(text)])


def test_command_has_slim_flags(tmp_path):
    cmd = build_command("claude", "haiku", "low", tmp_path / "p.md")
    for flag in ("--input-format", "--include-partial-messages", "--strict-mcp-config",
                 "--disable-slash-commands", "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--bare" not in cmd  # --bare wymaga klucza API


async def test_streams_and_keeps_process_warm(tmp_path):
    brain = make(tmp_path)
    await brain.start()
    pid = brain._proc.pid
    assert await collect(brain, "jak leci") == "Odpowiedź 1. Pytanie brzmiało: jak leci."
    assert await collect(brain, "drugie") == "Odpowiedź 2. Pytanie brzmiało: drugie."
    assert brain._proc.pid == pid  # ten sam proces, bez zimnego startu
    await brain.close()


async def test_reset_starts_fresh_process(tmp_path):
    brain = make(tmp_path)
    await brain.start()
    await collect(brain, "a")
    old = brain._proc.pid
    await brain.reset()
    assert (await collect(brain, "b")).startswith("Odpowiedź 1.")
    assert brain._proc.pid != old
    await brain.close()


async def test_context_is_prepended(tmp_path):
    brain = make(tmp_path, context=lambda: "pogoda: 5 stopni")
    await brain.start()
    assert (await collect(brain, "pytanie")).endswith("pytanie.")
    await brain.close()


async def test_error_result_raises(tmp_path):
    brain = make(tmp_path)
    await brain.start()
    with pytest.raises(BrainError):
        await collect(brain, "blad")
    assert (await collect(brain, "dalej")).startswith("Odpowiedź")
    await brain.close()


async def test_timeout_kills_and_recovers(tmp_path):
    brain = make(tmp_path, timeout_s=0.5)
    await brain.start()
    with pytest.raises(TimeoutError):
        await collect(brain, "zawies")
    assert (await collect(brain, "po awarii")).startswith("Odpowiedź 1.")
    await brain.close()
