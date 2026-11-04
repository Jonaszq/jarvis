#!/usr/bin/env python3
"""Sprawdza, dokąd idzie dźwięk: lista wyjść i 3-sekundowy sygnał na wybranym.

  python scripts/audio_check.py              lista urządzeń
  python scripts/audio_check.py default      sygnał na domyślnym wyjściu
  python scripts/audio_check.py pipewire     sygnał przez PipeWire (powinien iść na JBL)
"""
import sys

import numpy as np
import sounddevice as sd

print(sd.query_devices())
print("domyślne (wejście, wyjście):", sd.default.device)
if len(sys.argv) > 1:
    dev = None if sys.argv[1] == "default" else sys.argv[1]
    rate = 22050
    t = np.arange(rate * 3) / rate
    y = (0.3 * np.sin(2 * np.pi * 660 * t) * 32767).astype(np.int16)
    print(f"gram 3 s sygnału na: {sys.argv[1]}")
    sd.play(y, rate, device=dev, blocking=True)
    print("koniec")
