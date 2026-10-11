import array
import os
import sys
import wave
from importlib import import_module
from io import BytesIO
from threading import Lock

from langchain_community.document_loaders.blob_loaders import Blob
from langchain_community.document_loaders.parsers.audio import OpenAIWhisperParser

from app.timing import timed_phase


class TranscriptionServiceError(RuntimeError):
    pass


# Needle's native model state is shared by every Whistle instance in a process.
_whistle_lock = Lock()


class WhistleTranscriptionService:
    def __init__(self) -> None:
        self._model = None

    def transcribe(self, wav_data: bytes) -> str:
        try:
            with timed_phase("transcription_pcm_conversion"):
                with wave.open(BytesIO(wav_data), "rb") as wav:
                    if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                        raise ValueError("Whistle richiede WAV PCM mono 16 bit, 16000 Hz")
                    pcm = array.array("h")
                    pcm.frombytes(wav.readframes(wav.getnframes()))
                if sys.byteorder == "big":
                    pcm.byteswap()
                # Whistle interprets bytes as float32 samples, not as a WAV file.
                samples = array.array("f", (sample / 32768.0 for sample in pcm))
            with timed_phase("whistle_lock_wait"):
                _whistle_lock.acquire()
            try:
                if self._model is None:
                    with timed_phase("whistle_model_load"):
                        # Local inference must never download assets or register Hub usage.
                        os.environ["NEEDLE_TELEMETRY"] = "0"
                        os.environ["DO_NOT_TRACK"] = "1"
                        os.environ["HF_HUB_OFFLINE"] = "1"
                        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
                        # Hub may already have been imported by another dependency.
                        from huggingface_hub import constants as hub_constants
                        hub_constants.HF_HUB_OFFLINE = True
                        hub_constants.HF_HUB_DISABLE_TELEMETRY = True
                        self._model = import_module("needle").Whistle()
                with timed_phase("whistle_inference"):
                    result = self._model.transcribe(samples, language="it")
            finally:
                _whistle_lock.release()
            text = result.get("text") if isinstance(result, dict) else None
            if not isinstance(text, str):
                raise ValueError("Risposta Whistle non valida")
        except Exception as exc:
            raise TranscriptionServiceError(
                "Trascrizione Whistle fallita (solo locale); verifica pesi e runtime "
                "preparati con scripts/download_whistle.py"
            ) from exc

        transcript = text.strip()
        if not transcript:
            raise TranscriptionServiceError("Whistle ha restituito una trascrizione vuota")
        return transcript


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
