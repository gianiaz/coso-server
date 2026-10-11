import wave
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from flask import Flask, g
from app import create_app

import pytest

from app.services.speech_service import (
    MAX_FILENAME_STEM_LENGTH,
    SOX_EFFECTS,
    SpeechService,
    SpeechGenerationError,
    filename_for_text,
    slugify,
    text_for_speech,
)


@pytest.fixture(autouse=True)
def sox_run(monkeypatch):
    def process(arguments, **kwargs):
        with wave.open(arguments[3], "rb") as raw:
            params = raw.getparams()
        with wave.open(arguments[14], "wb") as processed:
            processed.setparams(params)
            processed.writeframes(b"\x01\x00" * params.nframes)
        return subprocess.CompletedProcess(arguments, 0)

    run = Mock(side_effect=process)
    monkeypatch.setattr("app.services.speech_service.subprocess.run", run)
    return run


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


def test_generates_wav_with_piper_and_reuses_model(tmp_path, sox_run, caplog):
    caplog.set_level("INFO")
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
    assert first_call.args[0] == "... Ciao, sono Coso, come stai?"
    assert voice.synthesize_wav.call_args_list[1].args[0] == "... Seconda risposta."
    assert first_call.kwargs["syn_config"].length_scale == 1.2
    assert sox_run.call_count == 2
    arguments = sox_run.call_args_list[0].args[0]
    assert arguments[:3] == ["sox", "-t", "wav"]
    assert arguments[4:14] == ["-t", "wav", "-e", "signed-integer", "-b", "16", "-c", "1", "-r", "22050"]
    assert arguments[15:] == list(SOX_EFFECTS)
    # Apply attenuation last: a later norm would undo the volume reduction.
    assert arguments[-2:] == ["vol", "0.5"]
    assert sox_run.call_args.kwargs == {
        "check": True, "capture_output": True, "text": True, "timeout": 90,
    }
    with wave.open(str(tmp_path / filename), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 22_050
        assert audio.getnframes() == 2205
        assert audio.readframes(1) == b"\x01\x00"
    assert not list(tmp_path.glob("*.tmp"))
    timing = [record.getMessage() for record in caplog.records if "[Timing]" in record.getMessage()]
    ended = [message.split("phase=")[1].split()[0] for message in timing
             if "event=phase_end" in message]
    assert ended == ["speech_lock_wait", "piper_model_load", "piper_synthesis",
                     "piper_wav_validation", "sox_processing", "wav_publication",
                     "speech_lock_wait", "piper_synthesis", "piper_wav_validation",
                     "sox_processing", "wav_publication"]


@pytest.mark.parametrize("failure", ["missing", "exit", "timeout", "invalid", "rate"])
def test_sox_failure_preserves_previous_wav_and_cleans_temporary(tmp_path, sox_run, failure):
    service = SpeechService(output_dir=tmp_path, model_path=tmp_path / "paola.onnx",
                            sox_path="/custom path/sox")
    destination = tmp_path / filename_for_text("Ciao")
    destination.write_bytes(b"previous-audio")
    voice = Mock()
    voice.synthesize_wav.side_effect = write_audio

    def fail(arguments, **kwargs):
        assert arguments[0] == "/custom path/sox"
        output = Path(arguments[14])
        output.write_bytes(b"partial-output")
        if failure == "missing":
            raise FileNotFoundError("sox")
        if failure == "exit":
            raise subprocess.CalledProcessError(1, arguments, stderr="bad effect")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, 90)
        if failure == "rate":
            with wave.open(str(output), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 100)
        return subprocess.CompletedProcess(arguments, 0)

    sox_run.side_effect = fail
    with patch("app.services.speech_service.PiperVoice.load", return_value=voice):
        with pytest.raises(SpeechGenerationError, match="SoX"):
            service.generate("Ciao")
    assert destination.read_bytes() == b"previous-audio"
    assert list(tmp_path.iterdir()) == [destination]


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


@pytest.mark.parametrize("fail", [False, True])
def test_speech_logs_runtime_and_audio_speed(tmp_path, caplog, monkeypatch, fail):
    caplog.set_level("INFO")
    clock = [100.0]
    monkeypatch.setattr("app.services.speech_service.perf_counter", lambda: clock[0])
    system_files = {
        "/proc/device-tree/model": "Raspberry Pi 3 Model B",
        "/sys/class/thermal/thermal_zone0/temp": "65000",
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq": "1200000",
    }
    monkeypatch.setattr("app.services.speech_service._read_system_file", system_files.get)
    monkeypatch.setattr("app.services.speech_service.platform.machine", lambda: "aarch64")
    monkeypatch.setattr("app.services.speech_service.os.cpu_count", lambda: 4)
    monkeypatch.setattr("app.services.speech_service.os.sched_getaffinity",
                        lambda _: {0, 1, 2, 3}, raising=False)
    voice = Mock()
    voice.session.get_providers.return_value = ["CPUExecutionProvider"]
    voice.session.get_session_options.return_value = SimpleNamespace(
        intra_op_num_threads=0, inter_op_num_threads=0,
        execution_mode="ORT_SEQUENTIAL", graph_optimization_level="ORT_ENABLE_ALL")

    def synthesize(*args, **kwargs):
        clock[0] += .25
        write_audio(*args, **kwargs)
        if fail:
            raise RuntimeError("test failure")

    voice.synthesize_wav.side_effect = synthesize
    service = SpeechService(output_dir=tmp_path, model_path=tmp_path / "it_IT-serena-high.onnx")
    with Flask(__name__).test_request_context("/ask"):
        g.timing_id = "speech-123"
        with patch("app.services.speech_service.PiperVoice.load", return_value=voice):
            if fail:
                with pytest.raises(SpeechGenerationError):
                    service.generate("Testo privato")
            else:
                service.generate("Testo privato")
                service.generate("Seconda risposta")
    messages = [record.getMessage() for record in caplog.records]
    runtime = [message for message in messages if message.startswith("[PiperRuntime]")]
    assert len(runtime) == 1
    info = json.loads(runtime[0].split(" ", 3)[3])
    assert info["model"] == "it_IT-serena-high"
    assert info["model_path"] == str(service.model_path.resolve())
    assert info["board"] == "Raspberry Pi 3 Model B"
    assert info["architecture"] == "aarch64"
    assert info["cpu_count"] == info["cpu_affinity_count"] == 4
    assert info["providers"] == ["CPUExecutionProvider"]
    assert info["intra_op_num_threads"] == info["inter_op_num_threads"] == 0
    records = [message for message in messages if message.startswith("[Speech]")]
    assert all("id=speech-123 " in message for message in runtime + records)
    assert len(records) == (1 if fail else 2)
    stats = json.loads(records[0].split(" ", 3)[3])
    assert stats["outcome"] == ("error" if fail else "ok")
    assert stats["piper_duration_ms"] == stats["generation_duration_ms"] == 250.0
    assert stats["cpu_temperature_c"] == stats["cpu_temperature_end_c"] == 65.0
    assert stats["cpu_frequency_mhz"] == 1200.0
    assert stats["model_loaded_this_request"] is True
    if fail:
        assert stats["audio_seconds"] is None and stats["piper_rtf"] is None
        assert stats["error_type"] == "RuntimeError"
    else:
        assert stats["audio_seconds"] == stats["final_audio_seconds"] == .1
        assert stats["piper_rtf"] == 2.5
        assert stats["sample_rate_hz"] == 22050
        assert stats["wav_bytes"] == 4454
        assert json.loads(records[1].split(" ", 3)[3])["model_loaded_this_request"] is False
    assert all("Testo privato" not in message for message in messages)


def test_preload_at_app_start_reuses_model_for_first_question(tmp_path, caplog):
    caplog.set_level("INFO")
    voice = Mock()
    voice.synthesize_wav.side_effect = write_audio
    model_path = tmp_path / "it_IT-serena-medium.onnx"
    with patch("app.services.speech_service.PiperVoice.load", return_value=voice) as load:
        app = create_app({"COSO_API_KEY": "test", "OPENAI_API_KEY": "",
                          "WHISTLE_PRELOAD": False,
                          "WAV_OUTPUT_DIR": str(tmp_path), "PIPER_MODEL_PATH": str(model_path),
                          "MEMORY_DB_PATH": str(tmp_path / "memory.db")})
        load.assert_called_once_with(str(model_path))
        voice.synthesize_wav.assert_not_called()
        service = app.extensions["speech_service"]
        service.preload()  # Idempotent: already initialized at app creation.
        service.generate("Ciao")
        load.assert_called_once_with(str(model_path))
    stats = [record.getMessage() for record in caplog.records
             if record.getMessage().startswith("[Speech]")]
    assert json.loads(stats[0].split(" ", 3)[3])["model_loaded_this_request"] is False


def test_preload_failure_prevents_server_start(tmp_path, caplog):
    caplog.set_level("INFO")
    with patch("app.services.speech_service.PiperVoice.load", side_effect=FileNotFoundError):
        with pytest.raises(SpeechGenerationError, match="Preload Piper fallito"):
            create_app({"COSO_API_KEY": "test", "OPENAI_API_KEY": "",
                        "WAV_OUTPUT_DIR": str(tmp_path), "PIPER_MODEL_PATH": str(tmp_path / "missing.onnx"),
                        "MEMORY_DB_PATH": str(tmp_path / "memory.db")})
    assert any("phase=piper_preload outcome=error" in record.getMessage() for record in caplog.records)
