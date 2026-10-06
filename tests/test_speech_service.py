from unittest.mock import patch

from app.services.speech_service import SpeechService, slugify


def test_slugify_italian_text():
    assert slugify("Ciao, sono Coso, come stai?") == "ciao-sono-coso-come-stai"


def test_generates_wav_with_espeak(tmp_path):
    service = SpeechService(output_dir=tmp_path, executable="espeak-ng", voice="it")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        with open(command[-1], "wb") as wav_file:
            wav_file.write(b"RIFF-test")

    with patch("app.services.speech_service.subprocess.run", fake_run):
        filename = service.generate("Ciao, sono Coso, come stai?")

    assert filename == "ciao-sono-coso-come-stai.wav"
    assert captured["command"][:5] == [
        "espeak-ng",
        "-v",
        "it",
        "Ciao, sono Coso, come stai?",
        "-w",
    ]
    assert captured["kwargs"] == {"check": True, "capture_output": True, "timeout": 30}
    assert (tmp_path / filename).read_bytes() == b"RIFF-test"
