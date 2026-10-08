from unittest.mock import patch

import pytest
from langchain_core.documents import Document

from app.services.transcription_service import (
    TranscriptionService,
    TranscriptionServiceError,
)


def make_service(api_key="test-key", parser=None):
    with patch(
        "app.services.transcription_service.OpenAIWhisperParser"
    ) as parser_class:
        parser_class.return_value = parser
        service = TranscriptionService(
            api_key=api_key,
            base_url="https://api.openai.com/v1/",
            model="gpt-4o-mini-transcribe",
        )
    return service, parser_class


def test_transcribes_wav_through_langchain_parser():
    class FakeParser:
        def __init__(self):
            self.blob = None

        def lazy_parse(self, blob):
            self.blob = blob
            yield Document(page_content=" Ciao dal microfono. ")

    parser = FakeParser()
    service, parser_class = make_service(parser=parser)

    transcript = service.transcribe(b"RIFF-test")

    parser_class.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        language="it",
        model="gpt-4o-mini-transcribe",
    )
    assert parser.blob.data == b"RIFF-test"
    assert parser.blob.mimetype == "audio/wav"
    assert str(parser.blob.path) == "question.wav"
    assert transcript == "Ciao dal microfono."


def test_requires_openai_key():
    service, parser_class = make_service(api_key="")
    parser_class.assert_not_called()

    with pytest.raises(TranscriptionServiceError):
        service.transcribe(b"RIFF-test")


def test_maps_parser_errors():
    class FailingParser:
        def lazy_parse(self, _blob):
            raise RuntimeError("failure")

    service, _ = make_service(parser=FailingParser())
    with pytest.raises(TranscriptionServiceError, match="Trascrizione OpenAI fallita"):
        service.transcribe(b"RIFF-test")


def test_rejects_empty_transcript():
    class EmptyParser:
        def lazy_parse(self, _blob):
            yield Document(page_content="   ")

    service, _ = make_service(parser=EmptyParser())
    with pytest.raises(TranscriptionServiceError, match="trascrizione vuota"):
        service.transcribe(b"RIFF-test")
