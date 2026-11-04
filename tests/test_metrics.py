import json

from jarvis.metrics import MetricsLog, Turn


def test_durations_relative_to_speech_end(tmp_path):
    t = iter([10.0, 10.4, 11.1, 11.3, 12.0])
    turn = Turn(clock=lambda: next(t))
    turn.marks["wake"] = 7.0
    turn.mark("speech_end")
    turn.mark("transcript")
    turn.mark("first_token")
    turn.mark("first_token")  # drugi raz się nie liczy
    turn.mark("first_audio")
    turn.info["heard"] = "test"
    rec = MetricsLog(tmp_path / "t.jsonl").write(turn)
    assert rec["since_speech_end"] == {"transcript": 0.4, "first_token": 1.1, "first_audio": 1.3}
    assert rec["speaking_s"] == 3.0
    line = json.loads((tmp_path / "t.jsonl").read_text())
    assert line["heard"] == "test"
