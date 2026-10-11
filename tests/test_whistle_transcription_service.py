import struct
import wave
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event, Lock
from unittest.mock import Mock, patch

import pytest

from app import create_app
from app.services.transcription_service import (
    TranscriptionService, TranscriptionServiceError, WhistleTranscriptionService,
)


def wav_bytes(samples=(-32768, -16384, 0, 16384, 32767), rate=16000):
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


def test_pcm_conversion_language_and_model_reuse():
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
