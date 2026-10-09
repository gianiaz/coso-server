import wave
from unittest.mock import Mock, patch

import pytest

from app.services.speech_service import (
    MAX_FILENAME_STEM_LENGTH,
    SpeechService,
    SpeechGenerationError,
    filename_for_text,
    slugify,
    text_for_speech,
)


def test_slugify_italian_text():
    assert slugify("Ciao, sono Coso, come stai?") == "ciao-sono-coso-come-stai"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Ciao, **Giovanni**! Ecco *Coso*.", "Ciao, Giovanni! Ecco Coso."),
        ("***Ciao***, __Giovanni__: _bene_ e ~~male~~.", "Ciao, Giovanni: bene e male."),
        ("## Consigli ##\n- **Primo**\n* Secondo\n1. Terzo", "Consigli Primo Secondo Terzo"),
        ("> Citazione\n\n---\n\n- [x] Fatto", "Citazione Fatto"),
        ("Titolo\n======\nTesto\n------", "Titolo Testo"),
        ("Leggi [qui](https://example.com/a_(b)).", "Leggi qui."),
        ("Usa `nome_file`:\n```python\nprint('ciao')\n```", "Usa nome_file: print('ciao')"),
        ("Temperatura: -5. 2 * 3 = 6. nome_file", "Temperatura: -5. 2 * 3 = 6. nome_file"),
        ("Ciao, sono Coso, come stai?", "Ciao, sono Coso, come stai?"),
    ],
)
def test_removes_markdown_for_speech(source, expected):
    assert text_for_speech(source) == expected


def test_rejects_text_without_spoken_content(tmp_path):
    service = SpeechService(
        output_dir=tmp_path, model_path=tmp_path / "paola.onnx"
    )
    with patch("app.services.speech_service.PiperVoice.load") as load:
        with pytest.raises(SpeechGenerationError, match="vuoto"):
            service.generate("---\n```\n```")
    load.assert_not_called()


def test_long_text_gets_bounded_unique_filename():
    first = filename_for_text("risposta molto lunga " * 30)
    second = filename_for_text("risposta molto lunga " * 29 + "diversa")

    assert len(first.removesuffix(".wav")) <= MAX_FILENAME_STEM_LENGTH
    assert first.endswith(".wav")
    assert first != second


def write_audio(text, wav_file, *, syn_config):
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(22_050)
    wav_file.writeframes(b"\x00\x00" * 2205)


def test_generates_wav_with_piper_and_reuses_model(tmp_path):
    service = SpeechService(
        output_dir=tmp_path, model_path=tmp_path / "paola.onnx", length_scale=1.2
    )
    voice = Mock()
    voice.synthesize_wav.side_effect = write_audio
    with patch("app.services.speech_service.PiperVoice.load", return_value=voice) as load:
        filename = service.generate("Ciao, sono **Coso**, come stai?")
        service.generate("Seconda risposta.")
    load.assert_called_once_with(str(tmp_path / "paola.onnx"))

    assert filename == "ciao-sono-coso-come-stai.wav"
    first_call = voice.synthesize_wav.call_args_list[0]
    assert first_call.args[0] == "Ciao, sono Coso, come stai?"
    assert first_call.kwargs["syn_config"].length_scale == 1.2
    with wave.open(str(tmp_path / filename), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 22_050
        assert audio.getnframes() == 2205
    assert not list(tmp_path.glob("*.tmp"))


def test_piper_failure_preserves_previous_wav_and_cleans_temporary(tmp_path):
    service = SpeechService(output_dir=tmp_path, model_path=tmp_path / "paola.onnx")
    destination = tmp_path / filename_for_text("Ciao")
    destination.write_bytes(b"previous-audio")
    voice = Mock()

    def fail_after_write(*args, **kwargs):
        write_audio(*args, **kwargs)
        raise RuntimeError("inference failed")

    voice.synthesize_wav.side_effect = fail_after_write
    with patch("app.services.speech_service.PiperVoice.load", return_value=voice):
        with pytest.raises(SpeechGenerationError, match="Piper"):
            service.generate("Ciao")
    assert destination.read_bytes() == b"previous-audio"
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_model_is_reported_as_speech_error(tmp_path):
    service = SpeechService(output_dir=tmp_path, model_path=tmp_path / "missing.onnx")
    with patch("app.services.speech_service.PiperVoice.load", side_effect=FileNotFoundError):
        with pytest.raises(SpeechGenerationError, match="Piper"):
            service.generate("Ciao")
    assert list(tmp_path.iterdir()) == []


def test_piper_empty_audio_is_rejected(tmp_path):
    service = SpeechService(output_dir=tmp_path, model_path=tmp_path / "paola.onnx")
    voice = Mock()

    def empty_audio(text, wav_file, *, syn_config):
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22_050)
        wav_file.writeframes(b"")

    voice.synthesize_wav.side_effect = empty_audio
    with patch("app.services.speech_service.PiperVoice.load", return_value=voice):
        with pytest.raises(SpeechGenerationError):
            service.generate("Ciao")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_invalid_piper_speed_is_rejected(tmp_path, scale):
    with pytest.raises(ValueError, match="PIPER_LENGTH_SCALE"):
        SpeechService(output_dir=tmp_path, model_path=tmp_path / "paola.onnx", length_scale=scale)
