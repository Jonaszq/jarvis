from jarvis.commands import USAGE, match
from jarvis.usage import UsageLog, report


def test_usage_window_and_report(tmp_path):
    now = [1_000_000.0]
    log = UsageLog(tmp_path / "u.jsonl", clock=lambda: now[0])
    log.add_result({"total_cost_usd": 3.0, "usage": {"input_tokens": 1}})  # stare, poza oknem
    now[0] += 6 * 3600
    for _ in range(3):
        log.add_result({"total_cost_usd": 0.25, "usage": {"input_tokens": 2000, "cache_read_input_tokens": 3000, "output_tokens": 100}})
    w = log.window(5)
    assert w["answers"] == 3 and abs(w["cost"] - 0.75) < 1e-9 and w["tokens"] == 15300
    text = report(log)
    assert "odpowiedziałem 3 razy" in text and "Claude: średnie" in text and "15 tysięcy tokenów" in text


def test_usage_command():
    assert match("Ile zużyłeś limitu?") == USAGE
    assert match("hey Jarvis, ile zużyłeś mi limitu w tej sesji?") == USAGE
    assert match("Sprawdź limit") == USAGE
    assert match("Jaki jest limit prędkości na autostradzie w Niemczech dzisiaj?") is None


def test_account_report():
    from datetime import datetime

    from jarvis.usage import TZ, account_report

    now = datetime(2026, 11, 4, 20, 45, tzinfo=TZ)
    text = account_report({"five_hour": {"utilization": 83.0, "resets_at": "2026-11-04T22:50:00Z"},
                           "seven_day": {"utilization": 35.4, "resets_at": "2026-11-05T19:00:00Z"}}, now)
    assert text.startswith("Sesja: zużyte 83 procent, dużo, odnowienie dziś o 23:50.")
    assert "Tydzień: zużyte 35 procent, spokojnie, odnowienie jutro o 20:00." in text
    assert account_report({}, now) == ""
