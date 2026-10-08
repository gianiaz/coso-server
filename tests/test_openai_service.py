from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.services.openai_service import ImageInput, OpenAIService, OpenAIServiceError


def make_service(api_key="test-key", chat_model=None):
    with patch("app.services.openai_service.ChatOpenAI") as chat_openai:
        chat_openai.return_value = chat_model
        service = OpenAIService(
            api_key=api_key,
            base_url="https://api.openai.com/v1/",
            model="test-model",
            instructions="Test instructions",
            max_output_tokens=123,
            timeout=4,
        )
    return service, chat_openai


def test_calls_responses_api_through_langchain_with_image():
    class FakeChatModel:
        def __init__(self):
            self.messages = None

        def invoke(self, messages):
            self.messages = messages
            return AIMessage(
                content=[{"type": "text", "text": "Vedo un test."}],
                response_metadata={"id": "resp_123", "model_name": "test-model-2026"},
            )

    chat_model = FakeChatModel()
    service, chat_openai = make_service(chat_model=chat_model)
    result = service.ask(
        text="Cosa vedi?", image=ImageInput(data=b"png", mime_type="image/png")
    )

    chat_openai.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="test-model",
        max_tokens=123,
        timeout=4,
        max_retries=0,
        store=False,
        use_responses_api=True,
        output_version="responses/v1",
    )
    assert isinstance(chat_model.messages[0], SystemMessage)
    assert chat_model.messages[0].content == "Test instructions"
    assert isinstance(chat_model.messages[1], HumanMessage)
    assert chat_model.messages[1].content[0] == {"type": "text", "text": "Cosa vedi?"}
    assert chat_model.messages[1].content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert result.text == "Vedo un test."
    assert result.model == "test-model-2026"
    assert result.response_id == "resp_123"


def test_uses_default_prompt_for_image_without_text():
    class FakeChatModel:
        def invoke(self, messages):
            assert messages[1].content[0]["text"] == "Descrivi questa immagine."
            return AIMessage(content="Descrizione")

    service, _ = make_service(chat_model=FakeChatModel())
    result = service.ask(
        text="", image=ImageInput(url="https://example.com/image.jpg")
    )

    assert result.text == "Descrizione"


def test_maps_langchain_errors():
    class FailingChatModel:
        def invoke(self, _messages):
            raise TimeoutError("timeout")

    service, _ = make_service(chat_model=FailingChatModel())
    with pytest.raises(OpenAIServiceError, match="Richiesta OpenAI fallita"):
        service.ask(text="Ciao")


def test_requires_openai_key():
    service, chat_openai = make_service(api_key="")
    chat_openai.assert_not_called()
    with pytest.raises(OpenAIServiceError):
        service.ask(text="Ciao")


def test_rejects_empty_openai_output():
    class EmptyChatModel:
        def invoke(self, _messages):
            return AIMessage(content=[])

    service, _ = make_service(chat_model=EmptyChatModel())
    with pytest.raises(OpenAIServiceError):
        service.ask(text="Ciao")


def test_injects_memory_as_a_separate_system_message():
    class FakeChatModel:
        def __init__(self):
            self.messages = None

        def invoke(self, messages):
            self.messages = messages
            return AIMessage(content="ESP32-S3")

    chat_model = FakeChatModel()
    service, _ = make_service(chat_model=chat_model)

    service.ask(
        text="Quale scheda uso?",
        memory_context="Memorie persistenti:\n- Il progetto usa una ESP32-S3.",
    )

    assert len(chat_model.messages) == 3
    assert isinstance(chat_model.messages[1], SystemMessage)
    assert "ESP32-S3" in chat_model.messages[1].content
    assert isinstance(chat_model.messages[2], HumanMessage)
