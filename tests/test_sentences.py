from jarvis.text.sentences import SentenceSplitter, split_all


def test_splits_on_sentence_end_as_tokens_arrive():
    s = SentenceSplitter()
    assert s.feed("Dziś jest ") == []
    assert s.feed("słonecznie. Jutro") == ["Dziś jest słonecznie."]
    assert s.feed(" pada! A") == ["Jutro pada!"]
    assert s.flush() == ["A"] or s.flush() == []


def test_flush_returns_rest():
    assert list(split_all(["Masz dwa spotkania", " o dziesiątej"])) == ["Masz dwa spotkania o dziesiątej"]


def test_long_sentence_cut_at_comma():
    text = "Jutro rano będzie zimno, około dwóch stopni na plusie, a po południu słońce wyjdzie zza chmur."
    out = list(split_all([text], max_words=8))
    assert out[0] == "Jutro rano będzie zimno,"
    assert " ".join(out) == text


def test_markdown_stripped():
    assert list(split_all(["**Uwaga**: test. "])) == ["Uwaga: test."]


def test_decimal_number_not_split():
    assert list(split_all(["Jest 2.5 stopnia. Koniec."])) == ["Jest 2.5 stopnia.", "Koniec."]
