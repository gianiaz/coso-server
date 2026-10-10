import hashlib
import re
import math
import subprocess
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
SOX_EFFECTS = (
    "norm", "-3", "highpass", "100", "pitch", "-40", "bass", "+2",
    "compand", "0.1,0.2", "6:-60,-30,-10", "-3", "-90", "0.1",
    "echo", "0.8", "0.8", "15", "0.3",
)


def validate_wav(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                or audio.getnframes() == 0 or audio.getcomptype() != "NONE"):
            raise SpeechGenerationError("WAV non valido: richiesto PCM mono a 16 bit non vuoto")
        return audio.getframerate()


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
        self, *, output_dir: Path, model_path: Path, length_scale: float = 1.25,
        sox_path: str = "sox",
    ) -> None:
        self.output_dir = output_dir
        self.model_path = model_path
        self.sox_path = sox_path
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
            processed = self.output_dir / f".{uuid4().hex}.processed.wav.tmp"
            try:
                if self._voice is None:
                    with timed_phase("piper_model_load"):
                        self._voice = PiperVoice.load(str(self.model_path))
                with wave.open(str(temporary), "wb") as wav_file:
                    self._voice.synthesize_wav(
                        "... " + text, wav_file, syn_config=self._synthesis_config
                    )
                sample_rate = validate_wav(temporary)
                with timed_phase("sox_processing"):
                    try:
                        subprocess.run(
                            [self.sox_path, "-t", "wav", str(temporary),
                             "-t", "wav", "-e", "signed-integer", "-b", "16",
                             "-c", "1", "-r", str(sample_rate), str(processed),
                             *SOX_EFFECTS],
                            check=True, capture_output=True, text=True, timeout=90,
                        )
                    except subprocess.CalledProcessError as exc:
                        raise SpeechGenerationError(
                            f"Elaborazione SoX fallita: {(exc.stderr or '')[-2000:]}"
                        ) from exc
                if validate_wav(processed) != sample_rate:
                    raise SpeechGenerationError("SoX ha modificato la frequenza del WAV")
                processed.replace(destination)
            except Exception as exc:
                raise SpeechGenerationError(
                    "Generazione WAV Piper/SoX fallita; verifica modello ONNX, "
                    "configurazione JSON ed eseguibile SoX"
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
                processed.unlink(missing_ok=True)

        return filename
