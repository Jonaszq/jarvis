#!/usr/bin/env bash
# Instalacja Jarvisa na Raspberry Pi (Debian 13). Można uruchamiać wiele razy.
#   git clone https://github.com/Jonaszq/jarvis ~/jarvis && cd ~/jarvis && ./scripts/install.sh
# Niczego nie kasuje: istniejący config.yaml, /etc/jarvis/env i modele zostają.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
VOICE_DIR="$ROOT/models"
VOICE="pl_PL-gosia-medium"
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/pl/pl_PL/gosia/medium"
WHISPER_MODEL="$HOME/whisper.cpp/models/ggml-base.bin"
WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"

step() { printf '\n== %s\n' "$*"; }

step "Pakiety systemowe"
NEED=()
for p in python3-venv libportaudio2 pipewire-alsa curl; do
  dpkg -s "$p" >/dev/null 2>&1 || NEED+=("$p")
done
if [ ${#NEED[@]} -gt 0 ]; then
  sudo apt-get update -qq && sudo apt-get install -y -qq "${NEED[@]}"
else
  echo "wszystko jest"
fi

step "Środowisko Pythona (.venv)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e . piper-tts onnxruntime scipy scikit-learn tqdm requests
# openWakeWord 0.4.0 bez zależności: nowsze wersje wymagają tflite-runtime,
# którego nie ma dla Pythona 3.13 na ARM. Ta wersja ma hey_jarvis w paczce.
.venv/bin/pip install -q --no-deps openwakeword==0.4.0

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

step "Wi-Fi bez oszczędzania energii (mniejsze opóźnienia sieci)"
if command -v nmcli >/dev/null; then
  CONN=$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2=="802-11-wireless"{print $1; exit}')
  if [ -n "${CONN:-}" ]; then
    sudo nmcli connection modify "$CONN" 802-11-wireless.powersave 2 && echo "power save wyłączony dla: $CONN (działa od następnego połączenia)"
  fi
fi

step "Usługa systemd (użytkownika)"
mkdir -p "$HOME/.config/systemd/user"
cp systemd/jarvis.service "$HOME/.config/systemd/user/jarvis.service"
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
# Nie włączamy automatycznego startu, dopóki stary ~/hej-claude też może używać mikrofonu.
# Gdy Jarvis będzie gotowy: systemctl --user enable --now jarvis

step "Sprawdzenie"
if command -v vcgencmd >/dev/null; then
  T=$(vcgencmd get_throttled | cut -d= -f2)
  [ "$T" = "0x0" ] || echo "UWAGA: zasilanie/throttling: $T (0x50005 = za niskie napięcie, wymień zasilacz lub kabel)"
fi
timedatectl show -p NTPSynchronized --value | grep -q yes || echo "UWAGA: zegar nie jest zsynchronizowany (NTP)"
command -v claude >/dev/null && echo "claude: $(claude --version 2>/dev/null)" || echo "UWAGA: brak 'claude' w PATH"
.venv/bin/python -m sounddevice | grep -i -E "usb|pnp" || echo "UWAGA: nie widzę mikrofonu USB"

cat <<MSG

Gotowe. Dalej:
  .venv/bin/python scripts/measure.py all     # pomiary kroków
  .venv/bin/python -m jarvis.main --text      # test bez mikrofonu
  systemctl --user enable --now jarvis        # start usługi (też po restarcie Pi)
  journalctl --user -u jarvis -f              # logi
MSG
