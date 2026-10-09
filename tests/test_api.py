import base64
import wave
from io import BytesIO
from pathlib import Path

import pytest

from app import DEFAULT_OPENAI_INSTRUCTIONS, create_app
from app.services.openai_service import AskResult, OpenAIServiceError
from app.services.transcription_service import TranscriptionServiceError


class FakeOpenAIService:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def ask(self, *, text, image=None):
        self.calls.append((text, image))
        if self.fail:
            raise OpenAIServiceError("boom")
        return AskResult(text="Risposta di prova", model="test-model", response_id="resp_test")


class FakeSpeechService:
    def __init__(self):
        self.calls = []

    def generate(self, text):
        self.calls.append(text)
        return "ciao-sono-coso-come-stai.wav"


class FakeTranscriptionService:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def transcribe(self, audio):
        self.calls.append(audio)
        if self.fail:
            raise TranscriptionServiceError("boom")
        return "Perché il cielo è blu?"


def make_wav(*, channels=1, sample_width=2, sample_rate=16_000, frames=3200):
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00" * frames * channels * sample_width)
    return output.getvalue()


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "COSO_API_KEY": "secret",
            "MAX_IMAGE_BYTES": 1024,
            "MAX_CONTENT_LENGTH": 20_000,
            "WAV_OUTPUT_DIR": str(tmp_path),
        }
    )
    application.extensions["openai_service"] = FakeOpenAIService()
    application.extensions["speech_service"] = FakeSpeechService()
    application.extensions["transcription_service"] = FakeTranscriptionService()
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def auth():
    return {"X-API-Key": "secret"}


def test_default_openai_instructions_describe_coso(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_INSTRUCTIONS", raising=False)
    application = create_app({"TESTING": True, "WAV_OUTPUT_DIR": str(tmp_path)})

    assert application.config["OPENAI_INSTRUCTIONS"] == DEFAULT_OPENAI_INSTRUCTIONS
    assert "Ti chiami Coso" in DEFAULT_OPENAI_INSTRUCTIONS
    assert "Giovanni" in DEFAULT_OPENAI_INSTRUCTIONS
    assert "Riccardo e Letizia" in DEFAULT_OPENAI_INSTRUCTIONS


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"service": "coso-server", "status": "ok"}


@pytest.mark.parametrize("greeting", ["hello", "wakeup"])
@pytest.mark.parametrize("public_base_url", ["", "https://coso.example/"])
def test_greeting_selects_existing_wav(client, app, monkeypatch, public_base_url, greeting):
    directory = Path(app.config["WAV_OUTPUT_DIR"]) / greeting
    directory.mkdir()
    audio = make_wav()
    for name in ("ciao.wav", "dimmi.wav"):
        (directory / name).write_bytes(audio)
    (directory / "notes.txt").write_text("ignored")
    (directory / "folder.wav").mkdir()
    app.config["WAV_PUBLIC_BASE_URL"] = public_base_url
    selections = iter(["ciao.wav", "dimmi.wav"])

    def choose(files):
        assert files == ["ciao.wav", "dimmi.wav"]
        return next(selections)

    monkeypatch.setattr("app.routes.random.choice", choose)
    origin = public_base_url.rstrip("/") or "http://192.168.1.50:8000"
    for name in ("ciao.wav", "dimmi.wav"):
        response = client.get(f"/{greeting}", base_url="http://192.168.1.50:8000")
        assert response.status_code == 200
        assert response.json == {
            "type": "command",
            "wav": f"{origin}/wav/{greeting}/{name}",
        }
        assert response.headers["Cache-Control"] == "no-store"
        download = client.get(response.json["wav"])
        assert download.status_code == 200
        assert download.mimetype == "audio/wav"
        assert download.data == audio

    assert app.extensions["speech_service"].calls == []
    assert app.extensions["openai_service"].calls == []


@pytest.mark.parametrize("greeting", ["hello", "wakeup"])
@pytest.mark.parametrize("directory_exists", [False, True])
def test_greeting_without_wav_returns_json_error(client, app, directory_exists, greeting):
    if directory_exists:
        (Path(app.config["WAV_OUTPUT_DIR"]) / greeting).mkdir()
    response = client.get(f"/{greeting}")
    assert response.status_code == 503
    assert response.json == {
        "error": f"{greeting}_unavailable",
        "message": "Nessun saluto WAV disponibile.",
    }


@pytest.mark.parametrize("greeting", ["hello", "wakeup"])
def test_greeting_download_missing_or_outside_directory(client, app, greeting):
    (Path(app.config["WAV_OUTPUT_DIR"]) / "private.wav").write_bytes(make_wav())
    for url in (f"/wav/{greeting}/missing.wav", f"/wav/{greeting}/../private.wav"):
        response = client.get(url)
        assert response.status_code == 404
        assert response.json["error"] == "not_found"


def test_ask_generates_wav_from_openai_answer(client, app):
    audio = make_wav()
    response = client.post(
        "/ask",
        data=audio,
        content_type="audio/wav",
        base_url="http://192.168.1.50:8000",
    )

    assert response.status_code == 200
    assert response.json == {
        "type": "command",
        "wav": "http://192.168.1.50:8000/wav/ciao-sono-coso-come-stai.wav",
    }
    assert app.extensions["transcription_service"].calls == [audio]
    assert app.extensions["openai_service"].calls == [("Perché il cielo è blu?", None)]
    assert app.extensions["speech_service"].calls == ["Risposta di prova"]


@pytest.mark.parametrize(
    ("payload", "content_type"),
    [
        (b"", "audio/wav"),
        (b"not-wav", "audio/wav"),
        (make_wav()[:-20], "audio/wav"),
        (make_wav(frames=1_600), "audio/wav"),
        (make_wav(channels=2), "audio/wav"),
        (make_wav(sample_width=1), "audio/wav"),
        (make_wav(sample_rate=8_000), "audio/wav"),
        (make_wav(), "application/octet-stream"),
    ],
    ids=[
        "empty",
        "not-wav",
        "truncated",
        "too-short",
        "stereo",
        "8-bit",
        "8-khz",
        "wrong-content-type",
    ],
)
def test_ask_rejects_invalid_audio(client, payload, content_type):
    response = client.post("/ask", data=payload, content_type=content_type)

    assert response.status_code == 400
    assert response.json["error"] == "validation_error"


def test_ask_maps_openai_errors(client, app):
    app.extensions["openai_service"] = FakeOpenAIService(fail=True)

    response = client.post("/ask", data=make_wav(), content_type="audio/wav")

    assert response.status_code == 502
    assert response.json["error"] == "upstream_error"


def test_ask_maps_transcription_errors(client, app):
    app.extensions["transcription_service"] = FakeTranscriptionService(fail=True)

    response = client.post("/ask", data=make_wav(), content_type="audio/wav")

    assert response.status_code == 502
    assert response.json["error"] == "transcription_error"
    assert app.extensions["openai_service"].calls == []


def test_serves_generated_wav(client, app):
    wav_path = app.config["WAV_OUTPUT_DIR"] + "/test.wav"
    with open(wav_path, "wb") as wav_file:
        wav_file.write(b"RIFF-test")

    response = client.get("/wav/test.wav")
    assert response.status_code == 200
    assert response.mimetype == "audio/wav"
    assert response.data == b"RIFF-test"


def test_requires_api_key(client):
    response = client.post("/api/v1/ask", json={"text": "Ciao"})
    assert response.status_code == 401


def test_accepts_text_json(client, app):
    response = client.post("/api/v1/ask", json={"text": "Ciao"}, headers=auth())
    assert response.status_code == 200
    assert response.json["answer"] == "Risposta di prova"
    assert app.extensions["openai_service"].calls[0][0] == "Ciao"


def test_accepts_image_url(client, app):
    response = client.post(
        "/api/v1/ask",
        json={"text": "Che cosa vedi?", "image_url": "https://example.com/image.jpg"},
        headers=auth(),
    )
    assert response.status_code == 200
    assert app.extensions["openai_service"].calls[0][1].url.endswith("image.jpg")


def test_accepts_base64_image(client, app):
    response = client.post(
        "/api/v1/ask",
        json={
            "image_base64": base64.b64encode(b"fake png").decode(),
            "image_mime_type": "image/png",
        },
        headers=auth(),
    )
    assert response.status_code == 200
    assert app.extensions["openai_service"].calls[0][1].data == b"fake png"


def test_accepts_multipart_image(client, app):
    response = client.post(
        "/api/v1/ask",
        data={"text": "Leggi", "image": (BytesIO(b"fake jpeg"), "photo.jpg", "image/jpeg")},
        headers=auth(),
    )
    assert response.status_code == 200
    assert app.extensions["openai_service"].calls[0][1].mime_type == "image/jpeg"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"image_url": "file:///etc/passwd"},
        {"image_base64": "not-base64", "image_mime_type": "image/png"},
        {"text": 42},
    ],
)
def test_rejects_invalid_json(client, payload):
    response = client.post("/api/v1/ask", json=payload, headers=auth())
    assert response.status_code == 400
    assert response.json["error"] == "validation_error"


def test_maps_upstream_errors(client, app):
    app.extensions["openai_service"] = FakeOpenAIService(fail=True)
    response = client.post("/api/v1/ask", json={"text": "Ciao"}, headers=auth())
    assert response.status_code == 502
    assert response.json["error"] == "upstream_error"
