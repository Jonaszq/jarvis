"""Udaje `claude -p --input-format stream-json --output-format stream-json`.

Na każdą linię JSON ze stdin odpowiada strumieniem zdarzeń jak prawdziwe CLI.
Pytanie "zawies" = brak odpowiedzi (test timeoutu), "blad" = wynik z błędem.
"""
import json
import sys
import time

turn = 0
for line in sys.stdin:
    msg = json.loads(line)
    text = msg["message"]["content"][0]["text"]
    turn += 1
    out = sys.stdout
    if turn == 1:
        out.write(json.dumps({"type": "system", "subtype": "init", "model": "haiku"}) + "\n")
    out.write("to nie jest json\n")
    if "zawies" in text:
        out.flush()
        time.sleep(60)
    if "blad" in text:
        out.write(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True}) + "\n")
        out.flush()
        continue
    answer = f"Odpowiedź {turn}. Pytanie brzmiało: {text.splitlines()[-1]}."
    for i in range(0, len(answer), 7):
        ev = {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
              "delta": {"type": "text_delta", "text": answer[i:i + 7]}}}
        out.write(json.dumps(ev, ensure_ascii=False) + "\n")
        out.flush()
    out.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}}, ensure_ascii=False) + "\n")
    out.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer}, ensure_ascii=False) + "\n")
    out.flush()
