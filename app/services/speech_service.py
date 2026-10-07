import hashlib
import re
import subprocess
import unicodedata
from pathlib import Path
from threading import Lock


class SpeechGenerationError(RuntimeError):
    pass


MAX_FILENAME_STEM_LENGTH = 120


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
        self, *, output_dir: Path, executable: str, voice: str, speed: int
    ) -> None:
        self.output_dir = output_dir
        self.executable = executable
        self.voice = voice
        self.speed = speed
        self._generation_lock = Lock()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, text: str) -> str:
        filename = filename_for_text(text)
        destination = self.output_dir / filename

        with self._generation_lock:
            try:
                completed_process = subprocess.run(
                    [
                        self.executable,
                        "-v",
                        self.voice,
                        "-s",
                        str(self.speed),
                        "-w",
                        str(destination),
                        text,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                if not destination.is_file() or destination.stat().st_size == 0:
                    stderr = completed_process.stderr.decode(errors="replace").strip()
                    detail = f": {stderr}" if stderr else ""
                    raise SpeechGenerationError(
                        f"espeak-ng non ha prodotto un file audio{detail}"
                    )
            except (FileNotFoundError, subprocess.SubprocessError, OSError, SpeechGenerationError) as exc:
                destination.unlink(missing_ok=True)
                raise SpeechGenerationError("Generazione WAV fallita") from exc

        return filename
