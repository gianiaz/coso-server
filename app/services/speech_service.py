import re
import subprocess
import unicodedata
from pathlib import Path
from threading import Lock


class SpeechGenerationError(RuntimeError):
    pass


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-") or "audio"


class SpeechService:
    def __init__(self, *, output_dir: Path, executable: str, voice: str) -> None:
        self.output_dir = output_dir
        self.executable = executable
        self.voice = voice
        self._generation_lock = Lock()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, text: str) -> str:
        filename = f"{slugify(text)}.wav"
        destination = self.output_dir / filename

        with self._generation_lock:
            try:
                subprocess.run(
                    [self.executable, "-v", self.voice, text, "-w", str(destination)],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                if not destination.is_file() or destination.stat().st_size == 0:
                    raise SpeechGenerationError("espeak-ng non ha prodotto un file audio")
            except (FileNotFoundError, subprocess.SubprocessError, OSError, SpeechGenerationError) as exc:
                destination.unlink(missing_ok=True)
                raise SpeechGenerationError("Generazione WAV fallita") from exc

        return filename
