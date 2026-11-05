import asyncio
from datetime import date, datetime

from jarvis.commands import FORGET, match
from jarvis.journal import TZ, Journal, summary_loop


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def test_add_forget_and_summary(tmp_path):
    c = Clock(datetime(2026, 11, 4, 19, 40, tzinfo=TZ))
    j = Journal(tmp_path / "journal", clock=c)
    j.add("zmęczony po szkole", "mood")
    c.dt = c.dt.replace(minute=45)
    j.add("skończył   kalendarz w Jarvisie", "done")
    text = j.read(date(2026, 11, 4))
    assert text.startswith("# Dziennik 2026-11-04 (środa)")
    assert "- 19:40 [nastrój] zmęczony po szkole" in text and "- 19:45 [zrobione] skończył kalendarz w Jarvisie" in text
    assert j.forget_last() == "skończył kalendarz w Jarvisie"
    assert len(j.entries(date(2026, 11, 4))) == 1
    j.write_summary(date(2026, 11, 4), "Dziś byłeś zmęczony.")
    j.add("jutro sprawdzian z Javy", "plan")  # wpis po podsumowaniu idzie nad nie
    text = j.read(date(2026, 11, 4))
    assert text.index("jutro sprawdzian") < text.index("## Podsumowanie dnia")
    assert j.summary(date(2026, 11, 4)) == "Dziś byłeś zmęczony."
    j.write_summary(date(2026, 11, 4), "Nowe.")  # nadpisuje, nie dubluje
    assert j.read(date(2026, 11, 4)).count("## Podsumowanie dnia") == 1
    assert (tmp_path / "journal").stat().st_mode & 0o077 == 0  # tylko właściciel


def test_forget_with_nothing(tmp_path):
    j = Journal(tmp_path, clock=lambda: datetime(2026, 11, 4, 12, tzinfo=TZ))
    assert j.forget_last() is None


def test_forget_command():
    assert match("Zapomnij to.") == FORGET
    assert match("hey Jarvis, nie zapisuj tego") == FORGET
    assert match("Zapomnij o tym, co mówiłem wczoraj o szkole i o wszystkim innym") is None


async def test_summary_loop_catches_up_yesterday(tmp_path):
    c = Clock(datetime(2026, 11, 4, 22, 0, tzinfo=TZ))
    j = Journal(tmp_path, clock=Clock(datetime(2026, 11, 3, 20, 0, tzinfo=TZ)))
    j.add("dobry dzień", "mood")
    done = []

    async def run(day):
        done.append(day)
        return "ok"

    task = asyncio.create_task(summary_loop(j, "23:00", run, clock=c))
    await asyncio.sleep(0.05)
    task.cancel()
    assert done == [date(2026, 11, 3)]
