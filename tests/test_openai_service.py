import json
from io import BytesIO
from unittest.mock import patch

import pytest

from app.services.openai_service import ImageInput, OpenAIService, OpenAIServiceError


class FakeHTTPResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def make_service(api_key="test-key"):
    return OpenAIService(
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        model="test-model",
        instructions="Test instructions",
        max_output_tokens=123,
        timeout=4,
    )


def test_calls_responses_api_with_image():
    api_response = {
        "id": "resp_123",
        "model": "test-model-2026",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Vedo un test."}]}
        ],
    }
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHTTPResponse(json.dumps(api_response).encode())

    with patch("app.services.openai_service.urlopen", fake_urlopen):
        result = make_service().ask(
            text="Cosa vedi?", image=ImageInput(data=b"png", mime_type="image/png")
        )

    payload = json.loads(captured["request"].data)
    assert captured["request"].full_url == "https://api.openai.com/v1/responses"
    assert captured["request"].headers["Authorization"] == "Bearer test-key"
    assert payload["store"] is False
    assert payload["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert captured["timeout"] == 4
    assert result.text == "Vedo un test."
    assert result.response_id == "resp_123"


def test_requires_openai_key():
    with pytest.raises(OpenAIServiceError):
        make_service(api_key="").ask(text="Ciao")


def test_rejects_empty_openai_output():
    response = FakeHTTPResponse(json.dumps({"id": "resp_empty", "output": []}).encode())
    with patch("app.services.openai_service.urlopen", return_value=response):
        with pytest.raises(OpenAIServiceError):
            make_service().ask(text="Ciao")
