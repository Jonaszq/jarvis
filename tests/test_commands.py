from jarvis.commands import END, SLEEP, WAKE, match


def test_matches_short_commands():
    assert match("Jarvis, wyłącz się.") == SLEEP
    assert match("hey Jarvis dezaktywuj się") == SLEEP
    assert match("Idź spać!") == SLEEP
    assert match("Hej Jarvis, obudź się.") == WAKE
    assert match("Dzięki, to wszystko.") == END
    assert match("Koniec") == END
    assert match("Dzięki.") == END
    assert match("Dziękuję bardzo!") == END
    assert match("Dzięki, a jaka jutro pogoda?") is None
    assert match("stop wyłącz się") == SLEEP


def test_ignores_normal_questions():
    assert match("Jak wyłączyć się z grupy na Facebooku i nie dostawać powiadomień?") is None
    assert match("Jaka jest pogoda?") is None
    assert match("Nic mi nie mów o kalendarzu na koniec tygodnia proszę") is None
    assert match("") is None
