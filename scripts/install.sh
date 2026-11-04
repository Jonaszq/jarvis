#!/usr/bin/env bash
# Instalacja Jarvisa na Raspberry Pi (Debian 13). Można uruchamiać wiele razy.
#   git clone https://github.com/Jonaszq/jarvis ~/jarvis && cd ~/jarvis && ./scripts/install.sh
# Niczego nie kasuje: istniejący config.yaml, /etc/jarvis/env i modele zostają.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
VOICE_DIR="$HOME/piper/voices"
VOICE="pl_PL-gosia-medium"
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/pl/pl_PL/gosia/medium"
WHISPER_MODEL="$HOME/whisper.cpp/models/ggml-base.bin"
WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"

step() { printf '\n== %s\n' "$*"; }

step "Pakiety systemowe"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-dev libportaudio2 curl

step "Środowisko Pythona (.venv)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e '.[pi]' piper-tts

step "Modele wake worda (openWakeWord)"
.venv/bin/python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

step "Polski głos Pipera ($VOICE)"
mkdir -p "$VOICE_DIR"
for f in "$VOICE.onnx" "$VOICE.onnx.json"; do
  [ -s "$VOICE_DIR/$f" ] || curl -fsSL -o "$VOICE_DIR/$f" "$VOICE_URL/$f"
done

step "Model Whispera (wielojęzyczny base)"
if [ -d "$HOME/whisper.cpp" ]; then
  [ -s "$WHISPER_MODEL" ] || curl -fsSL -o "$WHISPER_MODEL" "$WHISPER_URL"
else
  echo "Brak ~/whisper.cpp - lokalny STT nie zadziała, dopóki go nie zbudujesz."
fi

step "Konfiguracja"
[ -f config.yaml ] || cp config.example.yaml config.yaml
mkdir -p logs journal "$HOME/.jarvis-brain"
if [ ! -f /etc/jarvis/env ]; then
  sudo install -d -m 700 -o "$USER" -g "$USER" /etc/jarvis
  sudo install -m 600 -o "$USER" -g "$USER" /dev/null /etc/jarvis/env
  echo "Utworzono pusty /etc/jarvis/env (klucze API wpiszesz tam później)."
fi

step "Usługa systemd (użytkownika)"
mkdir -p "$HOME/.config/systemd/user"
cp systemd/jarvis.service "$HOME/.config/systemd/user/jarvis.service"
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable jarvis.service

step "Sprawdzenie"
command -v claude >/dev/null && echo "claude: $(claude --version 2>/dev/null)" || echo "UWAGA: brak 'claude' w PATH"
.venv/bin/python -m sounddevice | grep -i -E "usb|pnp" || echo "UWAGA: nie widzę mikrofonu USB"

cat <<MSG

Gotowe. Dalej:
  .venv/bin/python scripts/measure.py all     # pomiary kroków
  .venv/bin/python -m jarvis.main --text      # test bez mikrofonu
  systemctl --user start jarvis               # start usługi
  journalctl --user -u jarvis -f              # logi
MSG
