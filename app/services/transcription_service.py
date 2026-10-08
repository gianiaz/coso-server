from langchain_community.document_loaders.blob_loaders import Blob
from langchain_community.document_loaders.parsers.audio import OpenAIWhisperParser


class TranscriptionServiceError(RuntimeError):
    pass


class TranscriptionService:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        self._parser = (
            OpenAIWhisperParser(
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                language="it",
                model=model,
            )
            if api_key
            else None
        )

    def transcribe(self, wav_data: bytes) -> str:
        if self._parser is None:
            raise TranscriptionServiceError("OPENAI_API_KEY non configurata")

        blob = Blob.from_data(
            wav_data,
            mime_type="audio/wav",
            path="question.wav",
        )
        try:
            transcript = "\n".join(
                document.page_content.strip()
                for document in self._parser.lazy_parse(blob)
                if document.page_content.strip()
            )
        except Exception as exc:
            raise TranscriptionServiceError("Trascrizione OpenAI fallita") from exc

        if not transcript:
            raise TranscriptionServiceError("OpenAI ha restituito una trascrizione vuota")
        return transcript
