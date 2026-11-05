# Jarvis

Asystent głosowy po polsku na Raspberry Pi 4. Mówisz „hey Jarvis”, pytasz, a odpowiedź leci z głośnika.
Cel: 2–3 s od końca pytania do pierwszego słowa.

Mózgiem jest Claude Code CLI na subskrypcji konta. Działa jako jeden ciepły proces w trybie stream-json, bez klucza API.
Mowa i rozpoznawanie: ElevenLabs (Flash v2.5, Scribe), z automatycznym zapasem lokalnym (Piper, whisper.cpp), gdy brak internetu albo kończy się limit. Wake word: openWakeWord.

## Instalacja na Pi

```bash
git clone https://github.com/Jonaszq/jarvis ~/jarvis
cd ~/jarvis && ./scripts/install.sh
```

Sekrety (klucze API) trzymamy tylko w `/etc/jarvis/env` (tryb 600), nigdy w repo.
Ustawienia są w `config.yaml`, który tworzy się z `config.example.yaml`.
Aktualizacja: `./scripts/update.sh`.

## Uruchamianie i pomiary

```bash
.venv/bin/python scripts/measure.py all       # czas każdego kroku osobno
.venv/bin/python scripts/measure.py mic       # strojenie progu ciszy pod mikrofon
.venv/bin/python -m jarvis.main --text        # pytania z klawiatury, odpowiedź na głośnik
systemctl --user start jarvis                 # normalna praca
.venv/bin/python scripts/bench.py --last 20   # mediana i p90 z prawdziwych rozmów
```

## Budowa

- `jarvis/orchestrator.py`: pętla czuwanie → nagrywanie → myślenie → mówienie → dopytanie. Zdanie po zdaniu, równolegle.
- `jarvis/interfaces.py`: wspólne interfejsy STT / mózg / TTS, żeby lokalne i chmurowe wersje dało się podmieniać.
- `jarvis/brain/claude_code.py`: ciepły proces `claude` stream-json.
- `jarvis/stt/`, `jarvis/tts/`, `jarvis/audio/`: adaptery i obsługa dźwięku.
- `jarvis/metrics.py`: czasy każdej tury do `logs/turns.jsonl`.

Testy: `pip install -e '.[dev]' && pytest`.

## ElevenLabs

Klucz wpisz na Pi w `/etc/jarvis/env` jako `ELEVENLABS_API_KEY=...`, nigdy w repo.
Wybór głosu: `.venv/bin/python scripts/voices.py polish` (darmowe próbki polskich głosów) albo `mine` (głosy z konta mówiące po polsku).
Wybrany `voice_id` wpisz w `config.yaml` w sekcji `elevenlabs`.
