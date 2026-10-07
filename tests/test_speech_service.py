from unittest.mock import patch

from app.services.speech_service import (
    MAX_FILENAME_STEM_LENGTH,
    SpeechService,
    filename_for_text,
    slugify,
)


def test_slugify_italian_text():
    assert slugify("Ciao, sono Coso, come stai?") == "ciao-sono-coso-come-stai"


def test_long_text_gets_bounded_unique_filename():
    first = filename_for_text("risposta molto lunga " * 30)
    second = filename_for_text("risposta molto lunga " * 29 + "diversa")

    assert len(first.removesuffix(".wav")) <= MAX_FILENAME_STEM_LENGTH
    assert first.endswith(".wav")
    assert first != second


def test_generates_wav_with_espeak(tmp_path):
    service = SpeechService(
        output_dir=tmp_path, executable="espeak-ng", voice="it", speed=150
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        destination = command[command.index("-w") + 1]
        with open(destination, "wb") as wav_file:
            wav_file.write(b"RIFF-test")

    with patch("app.services.speech_service.subprocess.run", fake_run):
        filename = service.generate("Ciao, sono Coso, come stai?")

    assert filename == "ciao-sono-coso-come-stai.wav"
    assert captured["command"] == [
        "espeak-ng",
        "-v",
        "it",
        "-s",
        "150",
        "-w",
        str(tmp_path / filename),
        "Ciao, sono Coso, come stai?",
    ]
    assert captured["kwargs"] == {"check": True, "capture_output": True, "timeout": 30}
    assert (tmp_path / filename).read_bytes() == b"RIFF-test"
