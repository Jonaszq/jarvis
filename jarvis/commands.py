"""Komendy głosowe obsługiwane bez mózgu (od razu, bez czekania na Claude).

- END:   kończy rozmowę, Jarvis wraca do czekania na "hey Jarvis"
- SLEEP: usypia Jarvisa; ignoruje wszystko, dopóki nie usłyszy
         "hey Jarvis, obudź się"
- WAKE:  budzi z uśpienia
- FORGET: "zapomnij to" usuwa ostatni wpis z dziennika

Dopasowujemy tylko krótkie wypowiedzi (do 6 słów), żeby zdanie typu
"jak wyłączyć się z grupy na Facebooku" nie uśpiło Jarvisa.
"""

from __future__ import annotations

import re
import unicodedata

END = "end"
SLEEP = "sleep"
WAKE = "wake"
FORGET = "forget"

MAX_WORDS = 6

_PHRASES: dict[str, tuple[str, ...]] = {
    SLEEP: (
        "wyłącz się", "wyłączy się", "dezaktywuj się", "dezaktywacja", "deaktywuj się",
        "idź spać", "śpij", "uśpij się", "zaśnij", "wycisz się", "nie przeszkadzaj",
        "tryb uśpienia", "przestań słuchać",
    ),
    FORGET: ("zapomnij to", "zapomnij o tym", "zapomnij tamto", "nie zapisuj tego", "usuń to z dziennika",
             "wymaż to", "skasuj ostatni wpis", "usuń ostatni wpis"),
    WAKE: ("obudź się", "obudz się", "wstań", "wstawaj", "aktywuj się", "włącz się", "pobudka", "budzimy się",
           "jesteś tam", "budź się", "obudź"),
    END: (
        "koniec", "to wszystko", "dzięki to wszystko", "dziękuję to wszystko", "na tym koniec", "koniec rozmowy",
    ),
}

# Te kończą rozmowę tylko jako cała wypowiedź ("dzięki" tak, "dzięki, a jutro?" nie).
# Krótkie słowa, które łatwo trafiają się w zwykłym zdaniu ("nic się nie dzieje
# z pogodą"), też tylko jako cała wypowiedź.
_EXACT_END = {"dzięki", "dziękuję", "dzięki wielkie", "dziękuję bardzo", "dzięki bardzo", "ok dzięki",
              "okej dzięki", "super dzięki", "spoko", "dobra dzięki", "nic", "nieważne", "anuluj", "stop",
              "zamknij się", "cicho", "spadaj", "stop stop", "dobra stop", "cicho bądź", "to tyle", "wystarczy",
              "dobra wystarczy", "to tyle dzięki"}

_WAKE_PREFIX = re.compile(r"^(hej|hey|ej|ok|okej)?\s*(jarvis|dżarwis|jarwis|dżarvis)\b[\s,!.]*")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text.lower())
    text = re.sub(r"[^\w\sąćęłńóśźż]", " ", text)
    text = " ".join(text.split())
    return _WAKE_PREFIX.sub("", text).strip()


def match(text: str) -> str | None:
    norm = normalize(text)
    if not norm or len(norm.split()) > MAX_WORDS:
        return None
    if norm in _EXACT_END:
        return END
    padded = f" {norm} "
    for command in (FORGET, SLEEP, WAKE, END):  # SLEEP przed END: "stop, wyłącz się" ma uśpić
        for phrase in _PHRASES[command]:
            # Jedno słowo ("koniec", "śpij") tylko jako cała wypowiedź: "pogoda na
            # koniec tygodnia" nie może kończyć rozmowy.
            if " " not in phrase:
                if norm == phrase:
                    return command
            elif f" {phrase} " in padded:
                return command
    return None
