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


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "COSO_API_KEY": "secret",
            "MAX_IMAGE_BYTES": 1024,
            "MAX_CONTENT_LENGTH": 2048,
        }
    )
    application.extensions["openai_service"] = FakeOpenAIService()
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
