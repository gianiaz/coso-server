import struct
import socket
import wave
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event, Lock
from unittest.mock import Mock, patch

import pytest
from huggingface_hub import constants as hub_constants

from app import create_app
from app.services.transcription_service import (
    TranscriptionService, TranscriptionServiceError, WhistleTranscriptionService,
)


@pytest.fixture(autouse=True)
def restore_offline_settings(monkeypatch):
    # The adapter intentionally forces these process-wide settings.
    for name in ("NEEDLE_TELEMETRY", "DO_NOT_TRACK", "HF_HUB_OFFLINE",
                 "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setattr(hub_constants, "HF_HUB_OFFLINE", False)
    monkeypatch.setattr(hub_constants, "HF_HUB_DISABLE_TELEMETRY", False)


def wav_bytes(samples=(-32768, -16384, 0, 16384, 32767), rate=16000):
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


def test_pcm_conversion_language_and_model_reuse(caplog):
    caplog.set_level("INFO")
    model = Mock()
    model.transcribe.return_value = {"text": " Ciao Coso! "}
    with patch("app.services.transcription_service.import_module") as load:
        load.return_value.Whistle.return_value = model
        service = WhistleTranscriptionService()
        load.assert_not_called()
        assert service.transcribe(wav_bytes()) == "Ciao Coso!"
        assert service.transcribe(wav_bytes()) == "Ciao Coso!"
        load.return_value.Whistle.assert_called_once_with()
    samples = model.transcribe.call_args.args[0]
    assert samples.typecode == "f"
    assert list(samples) == pytest.approx([-1, -.5, 0, .5, 32767 / 32768])
    assert model.transcribe.call_args.kwargs == {"language": "it"}
    timing = [record.getMessage() for record in caplog.records if "[Timing]" in record.getMessage()]
    ended = [message.split("phase=")[1].split()[0] for message in timing
             if "event=phase_end" in message]
    assert ended == ["transcription_pcm_conversion", "whistle_lock_wait",
                     "whistle_model_load", "whistle_inference",
                     "transcription_pcm_conversion", "whistle_lock_wait", "whistle_inference"]


@pytest.mark.parametrize("result", [{"text": " "}, {}, {"text": None}, {"text": 42}, None])
def test_rejects_empty_or_malformed_output(result):
    service = WhistleTranscriptionService()
    service._model = Mock()
    service._model.transcribe.return_value = result
    with pytest.raises(TranscriptionServiceError):
        service.transcribe(wav_bytes())


def test_initialization_failure_is_mapped_and_retried():
    service = WhistleTranscriptionService()
    with patch("app.services.transcription_service.import_module") as load:
        load.return_value.Whistle.side_effect = [RuntimeError("download failed"), Mock()]
        with pytest.raises(TranscriptionServiceError, match="Whistle fallita"):
            service.transcribe(wav_bytes())
        load.return_value.Whistle.side_effect = None
        load.return_value.Whistle.return_value.transcribe.return_value = {"text": "Ciao"}
        assert service.transcribe(wav_bytes()) == "Ciao"


def test_native_failure_is_mapped():
    service = WhistleTranscriptionService()
    service._model = Mock()
    service._model.transcribe.side_effect = RuntimeError("native failure")
    with pytest.raises(TranscriptionServiceError, match="Whistle fallita"):
        service.transcribe(wav_bytes())


@pytest.mark.parametrize("audio", [b"invalid", wav_bytes(rate=8000)])
def test_rejects_invalid_audio_before_loading_model(audio):
    with patch("app.services.transcription_service.import_module") as load:
        with pytest.raises(TranscriptionServiceError):
            WhistleTranscriptionService().transcribe(audio)
        load.assert_not_called()


def test_process_lock_serializes_two_service_instances():
    entered, release, second_started = Event(), Event(), Event()
    active_lock = Lock()

    def transcribe(_samples, **_kwargs):
        assert active_lock.acquire(blocking=False), "concurrent native calls"
        try:
            entered.set()
            assert release.wait(5)
            return {"text": "Ciao"}
        finally:
            active_lock.release()

    first, second = WhistleTranscriptionService(), WhistleTranscriptionService()
    first._model = Mock(transcribe=transcribe)
    second._model = Mock(transcribe=transcribe)

    def second_call():
        second_started.set()
        return second.transcribe(wav_bytes())

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.transcribe, wav_bytes())
        try:
            assert entered.wait(5)
            b = pool.submit(second_call)
            assert second_started.wait(5)
            assert not b.done()
        finally:
            release.set()
        assert a.result(timeout=5) == b.result(timeout=5) == "Ciao"


@pytest.mark.parametrize("backend,cls", [("whistle", WhistleTranscriptionService),
                                         ("openai", TranscriptionService)])
def test_app_selects_backend(tmp_path, backend, cls):
    app = create_app({"COSO_API_KEY": "test", "WAV_OUTPUT_DIR": str(tmp_path),
                      "TRANSCRIPTION_BACKEND": backend, "OPENAI_API_KEY": ""})
    assert isinstance(app.extensions["transcription_service"], cls)


def test_app_rejects_unknown_backend(tmp_path):
    with pytest.raises(RuntimeError, match="TRANSCRIPTION_BACKEND"):
        create_app({"COSO_API_KEY": "test", "WAV_OUTPUT_DIR": str(tmp_path),
                    "TRANSCRIPTION_BACKEND": "typo"})


@pytest.mark.parametrize("cached", [True, False])
def test_real_needle_initialization_never_opens_network(tmp_path, monkeypatch, cached):
    # Use the installed Needle/Hub loading code, substituting only native inference.
    import needle
    from needle.agent import fetch, whistle

    monkeypatch.setattr(fetch, "cache_dir", lambda generation: str(tmp_path / str(generation)))
    monkeypatch.delenv("NEEDLE_WHISTLE_WEIGHTS", raising=False)
    weights = tmp_path / fetch.WHISTLE / fetch.base_weights(fetch.WHISTLE)
    if cached:
        weights.parent.mkdir(parents=True)
        weights.write_bytes(b"local-model")
    native = Mock()
    native.needle_load.return_value = 0

    def native_transcribe(*args):
        args[5].value = b'{"text":"Ciao"}'
        return 0

    native.needle_transcribe.side_effect = native_transcribe
    monkeypatch.setattr(whistle, "_lib", lambda: native)
    monkeypatch.setattr(whistle, "_loaded", None)
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")) as connect:
        service = WhistleTranscriptionService()
        if cached:
            assert service.transcribe(wav_bytes()) == "Ciao"
            assert service.transcribe(wav_bytes()) == "Ciao"
            native.needle_load.assert_called_once()
        else:
            with pytest.raises(TranscriptionServiceError, match="solo locale"):
                service.transcribe(wav_bytes())
            native.needle_load.assert_not_called()
        connect.assert_not_called()
    assert hub_constants.HF_HUB_OFFLINE is True
    assert hub_constants.HF_HUB_DISABLE_TELEMETRY is True
