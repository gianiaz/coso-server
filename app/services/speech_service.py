import hashlib
import re
import math
import wave
import unicodedata
from uuid import uuid4
from pathlib import Path
from threading import Lock
from piper import PiperVoice, SynthesisConfig

from app.timing import timed_phase


class SpeechGenerationError(RuntimeError):
    pass


MAX_FILENAME_STEM_LENGTH = 120


def text_for_speech(text: str) -> str:
    """Remove common Markdown formatting while retaining the spoken content."""
    # Fences and block markers should not be read as part of the answer.
    text = re.sub(r"(?m)^\s{0,3}(?:`{3,}|~{3,})[^\n]*$", "", text)
    text = re.sub(r"(?m)^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$", "", text)
    text = re.sub(r"(?m)^\s{0,3}(?:=+|-+)\s*$", "", text)
    text = re.sub(r"(?m)^\s{0,3}(?:>\s*)+", "", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?$", r"\1", text)
    text = re.sub(r"(?m)^\s*(?:[-+*]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", "", text)
    # Keep the label instead of speaking the destination URL.
    text = re.sub(r"!?\[([^\]\n]*)\]\((?:[^()\n]|\([^()\n]*\))*\)", r"\1", text)
    # Work from outer to inner emphasis, including ***bold italic***.
    for marker in ("***", "___", "**", "__", "~~", "*", "_"):
        escaped = re.escape(marker)
        text = re.sub(
            rf"(?<!\w){escaped}(?=\S)(.+?)(?<=\S){escaped}(?!\w)",
            r"\1",
            text,
            flags=re.DOTALL,
        )
    text = re.sub(r"`+([^`]+)`+", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-") or "audio"


def filename_for_text(text: str) -> str:
    stem = slugify(text)
    if len(stem) > MAX_FILENAME_STEM_LENGTH:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        prefix_length = MAX_FILENAME_STEM_LENGTH - len(digest) - 1
        stem = f"{stem[:prefix_length].rstrip('-')}-{digest}"
    return f"{stem}.wav"


class SpeechService:
    def __init__(
        self, *, output_dir: Path, model_path: Path, length_scale: float = 1.25
    ) -> None:
        self.output_dir = output_dir
        self.model_path = model_path
        if not math.isfinite(length_scale) or length_scale <= 0:
            raise ValueError("PIPER_LENGTH_SCALE deve essere un numero positivo finito")
        self._synthesis_config = SynthesisConfig(length_scale=length_scale)
        self._voice = None
        self._generation_lock = Lock()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, text: str) -> str:
        text = text_for_speech(text)
        if not text:
            raise SpeechGenerationError("Il testo da sintetizzare è vuoto")
        filename = filename_for_text(text)
        destination = self.output_dir / filename

        with self._generation_lock:
            temporary = self.output_dir / f".{uuid4().hex}.wav.tmp"
            try:
                if self._voice is None:
                    with timed_phase("piper_model_load"):
                        self._voice = PiperVoice.load(str(self.model_path))
                with wave.open(str(temporary), "wb") as wav_file:
                    self._voice.synthesize_wav(
                        "... " + text, wav_file, syn_config=self._synthesis_config
                    )
                with wave.open(str(temporary), "rb") as wav_file:
                    if (wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2
                            or wav_file.getnframes() == 0):
                        raise SpeechGenerationError("Piper non ha prodotto un WAV PCM mono a 16 bit")
                temporary.replace(destination)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                raise SpeechGenerationError(
                    "Generazione WAV Piper fallita; verifica modello ONNX e configurazione JSON"
                ) from exc

        return filename
