import base64
from io import BytesIO

import pytest

from app import create_app
from app.services.openai_service import AskResult, OpenAIServiceError


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


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "COSO_API_KEY": "secret",
            "MAX_IMAGE_BYTES": 1024,
            "MAX_CONTENT_LENGTH": 2048,
            "WAV_OUTPUT_DIR": str(tmp_path),
        }
    )
    application.extensions["openai_service"] = FakeOpenAIService()
    application.extensions["speech_service"] = FakeSpeechService()
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def auth():
    return {"X-API-Key": "secret"}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"service": "coso-server", "status": "ok"}


def test_hello_generates_wav_command(client, app):
    response = client.get("/hello", base_url="http://192.168.1.50:8000")
    assert response.status_code == 200
    assert response.json == {
        "type": "command",
        "wav": "http://192.168.1.50:8000/wav/ciao-sono-coso-come-stai.wav",
    }
    assert app.extensions["speech_service"].calls == ["Ciao, sono Coso, come stai?"]


def test_ask_generates_wav_from_openai_answer(client, app):
    response = client.post(
        "/ask",
        json={"question": "Perché il cielo è blu?"},
        base_url="http://192.168.1.50:8000",
    )

    assert response.status_code == 200
    assert response.json == {
        "type": "command",
        "wav": "http://192.168.1.50:8000/wav/ciao-sono-coso-come-stai.wav",
    }
    assert app.extensions["openai_service"].calls == [("Perché il cielo è blu?", None)]
    assert app.extensions["speech_service"].calls == ["Risposta di prova"]


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"question": ""}, {"question": "   "}, {"question": 42}],
)
def test_ask_rejects_invalid_question(client, payload):
    response = client.post("/ask", json=payload)

    assert response.status_code == 400
    assert response.json["error"] == "validation_error"


def test_ask_maps_openai_errors(client, app):
    app.extensions["openai_service"] = FakeOpenAIService(fail=True)

    response = client.post("/ask", json={"question": "Ciao"})

    assert response.status_code == 502
    assert response.json["error"] == "upstream_error"


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
