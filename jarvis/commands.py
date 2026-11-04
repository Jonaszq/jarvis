"""Komendy głosowe obsługiwane bez mózgu (od razu, bez czekania na Claude).

- END:   kończy rozmowę, Jarvis wraca do czekania na "hey Jarvis"
- SLEEP: usypia Jarvisa; ignoruje wszystko, dopóki nie usłyszy
         "hey Jarvis, obudź się"
- WAKE:  budzi z uśpienia

Dopasowujemy tylko krótkie wypowiedzi (do 6 słów), żeby zdanie typu
"jak wyłączyć się z grupy na Facebooku" nie uśpiło Jarvisa.
"""

from __future__ import annotations

import re
import unicodedata

END = "end"
SLEEP = "sleep"
WAKE = "wake"

MAX_WORDS = 6

_PHRASES: dict[str, tuple[str, ...]] = {
    SLEEP: (
        "wyłącz się", "wyłączy się", "dezaktywuj się", "dezaktywacja", "deaktywuj się",
        "idź spać", "śpij", "uśpij się", "zaśnij", "wycisz się", "nie przeszkadzaj",
        "tryb uśpienia", "przestań słuchać",
    ),
    WAKE: ("obudź się", "wstań", "aktywuj się", "włącz się", "pobudka", "budzimy się"),
    END: (
        "koniec", "to wszystko", "to tyle", "wystarczy", "dzięki to wszystko", "dziękuję to wszystko",
        "spadaj", "spoko dzięki", "nic", "nieważne", "anuluj", "stop", "zamknij się", "cicho",
    ),
}

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
    padded = f" {norm} "
    for command in (SLEEP, WAKE, END):  # SLEEP przed END: "stop, wyłącz się" ma uśpić
        for phrase in _PHRASES[command]:
            if f" {phrase} " in padded:
                return command
    return None
