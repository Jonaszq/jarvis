"""Cięcie strumienia tokenów na zdania dla TTS.

Mózg oddaje tekst kawałkami. Żeby głośnik mógł zacząć mówić wcześniej,
wysyłamy do TTS każde zdanie od razu, gdy jest kompletne. Bardzo długie
zdania tniemy po przecinku albo po limicie słów.
"""

from __future__ import annotations

import re
from typing import AsyncIterator, Iterable, Iterator

_END = re.compile(r"[.!?…]+[\"'”»)]*\s+")
_SOFT = re.compile(r"[,;:–—]\s+")


class SentenceSplitter:
    def __init__(self, max_words: int = 12, min_chars: int = 2, first_max_words: int | None = None):
        self.max_words = max_words
        self.min_chars = min_chars
        # Lokalny TTS (Piper) mówi dopiero po zsyntezowaniu całego kawałka, więc
        # pierwszy kawałek robimy krótki: szybciej słychać pierwsze słowo.
        self.first_max_words = first_max_words
        self._emitted = 0
        self._buf = ""

    def feed(self, chunk: str) -> list[str]:
        self._buf += chunk
        out: list[str] = []
        while True:
            sentence = self._take()
            if sentence is None:
                break
            if len(sentence) >= self.min_chars:
                out.append(sentence)
                self._emitted += 1
        return out

    def flush(self) -> list[str]:
        rest = _clean(self._buf)
        self._buf = ""
        return [rest] if len(rest) >= self.min_chars else []

    def _take(self) -> str | None:
        first = self.first_max_words and self._emitted == 0
        m = _END.search(self._buf)
        if first:
            end = m.end() if m else len(self._buf)
            words = self._buf[:end].split()
            # Za długie pierwsze zdanie (albo jeszcze bez kropki): tniemy po
            # przecinku w pierwszych ~10 słowach, a bez przecinka po first_max_words.
            if len(words) > self.first_max_words:
                head = " ".join(self._buf.split(" ")[: max(self.first_max_words, 10)])
                soft = [x for x in _SOFT.finditer(head) if len(head[: x.end()].split()) >= 2]
                if soft:
                    cut = soft[0].end()
                elif m and len(words) <= 10:
                    cut = m.end()
                else:
                    cut = len(" ".join(self._buf.split(" ")[: self.first_max_words])) + 1
                sentence, self._buf = self._buf[:cut], self._buf[cut:]
                return _clean(sentence)
        if m:
            sentence, self._buf = self._buf[: m.end()], self._buf[m.end():]
            return _clean(sentence)
        # Zbyt długie zdanie bez kropki: tniemy po miękkiej przerwie
        # albo twardo po limicie słów, ale tylko na granicy słowa.
        words = self._buf.split(" ")
        if len(words) <= self.max_words:
            return None
        head = " ".join(words[: self.max_words])
        soft = list(_SOFT.finditer(head))
        if soft:
            cut = soft[-1].end()
        else:
            cut = len(head) + 1
        sentence, self._buf = self._buf[:cut], self._buf[cut:]
        return _clean(sentence)


_URL = re.compile(r"https?://\S+|www\.\S+")


def _clean(text: str) -> str:
    text = _URL.sub("", text)
    for ch in "*#`\\_|<>[]{}":
        text = text.replace(ch, " " if ch in "_|" else "")
    text = " ".join(text.split())
    # Dopisek ze źródłami ma sens na ekranie, nie w głośniku.
    if text.lower().startswith(("źródła", "źródło", "sources")):
        return ""
    return text


def split_all(chunks: Iterable[str], **kwargs) -> Iterator[str]:
    splitter = SentenceSplitter(**kwargs)
    for chunk in chunks:
        yield from splitter.feed(chunk)
    yield from splitter.flush()


async def split_stream(chunks: AsyncIterator[str], **kwargs) -> AsyncIterator[str]:
    splitter = SentenceSplitter(**kwargs)
    async for chunk in chunks:
        for sentence in splitter.feed(chunk):
            yield sentence
    for sentence in splitter.flush():
        yield sentence
