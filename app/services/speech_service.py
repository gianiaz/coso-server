import hashlib
import re
import math
import os
import platform
import struct
import subprocess
import wave
import unicodedata
from uuid import uuid4
from pathlib import Path
from threading import Lock
from time import perf_counter
from piper import PiperVoice, SynthesisConfig

from app.timing import log_statistics, timed_phase


class SpeechGenerationError(RuntimeError):
    pass


MAX_FILENAME_STEM_LENGTH = 120
SOX_EFFECTS = (
    "norm", "-3", "highpass", "100", "pitch", "-40", "bass", "+2",
    "compand", "0.1,0.2", "6:-60,-30,-10", "-3", "-90", "0.1",
    "echo", "0.8", "0.8", "15", "0.3",
    # Attenuate after normalization and all other effects to retain half amplitude.
    "vol", "0.5",
)


def _read_system_file(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip("\x00\n ")
    except (OSError, UnicodeError):
        return None


def _system_number(path: str, divisor: float) -> float | None:
    value = _read_system_file(path)
    try:
        return round(float(value) / divisor, 1) if value is not None else None
    except ValueError:
        return None


def _cpu_state() -> dict:
    return {
        "cpu_temperature_c": _system_number("/sys/class/thermal/thermal_zone0/temp", 1000),
        "cpu_frequency_mhz": _system_number(
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", 1000),
    }


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

    def _log_runtime(self) -> None:
        stats = {
            "model_path": str(self.model_path.resolve()),
            "model": self.model_path.stem,
            "board": _read_system_file("/proc/device-tree/model"),
            "architecture": platform.machine(),
            "python_bits": struct.calcsize("P") * 8,
            "cpu_count": os.cpu_count(),
        }
        try:
            stats["cpu_affinity_count"] = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            stats["cpu_affinity_count"] = None
        try:
            session = self._voice.session
            options = session.get_session_options()
            stats.update(
                providers=session.get_providers(),
                intra_op_num_threads=int(options.intra_op_num_threads),
                inter_op_num_threads=int(options.inter_op_num_threads),
                execution_mode=str(options.execution_mode),
                graph_optimization_level=str(options.graph_optimization_level),
            )
        except Exception as exc:
            # Diagnostic introspection must not stop a working synthesis.
            stats["session_info_error"] = type(exc).__name__
        log_statistics("PiperRuntime", stats)

    def generate(self, text: str) -> str:
        text = text_for_speech(text)
        if not text:
            raise SpeechGenerationError("Il testo da sintetizzare è vuoto")
        filename = filename_for_text(text)
        destination = self.output_dir / filename
        generation_started = perf_counter()
        stats = {
            "model": self.model_path.stem,
            "length_scale": self._synthesis_config.length_scale,
            "text_characters": len(text),
            "model_loaded_this_request": False,
            "outcome": "error",
            "audio_seconds": None,
            "piper_duration_ms": None,
            "piper_rtf": None,
            **_cpu_state(),
        }

        with timed_phase("speech_lock_wait"):
            self._generation_lock.acquire()
        try:
            temporary = self.output_dir / f".{uuid4().hex}.wav.tmp"
            processed = self.output_dir / f".{uuid4().hex}.processed.wav.tmp"
            try:
                if self._voice is None:
                    with timed_phase("piper_model_load"):
                        self._voice = PiperVoice.load(str(self.model_path))
                    stats["model_loaded_this_request"] = True
                    self._log_runtime()
                synthesis_started = perf_counter()
                try:
                    with timed_phase("piper_synthesis"):
                        with wave.open(str(temporary), "wb") as wav_file:
                            self._voice.synthesize_wav(
                                "... " + text, wav_file, syn_config=self._synthesis_config
                            )
                finally:
                    synthesis_seconds = perf_counter() - synthesis_started
                    stats["piper_duration_ms"] = round(synthesis_seconds * 1000, 1)
                with timed_phase("piper_wav_validation"):
                    sample_rate = validate_wav(temporary)
                    with wave.open(str(temporary), "rb") as wav_file:
                        audio_seconds = wav_file.getnframes() / wav_file.getframerate()
                    stats["audio_seconds"] = round(audio_seconds, 3)
                    stats["sample_rate_hz"] = sample_rate
                    stats["piper_rtf"] = round(synthesis_seconds / audio_seconds, 4)
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
                with timed_phase("wav_publication"):
                    if validate_wav(processed) != sample_rate:
                        raise SpeechGenerationError("SoX ha modificato la frequenza del WAV")
                    with wave.open(str(processed), "rb") as wav_file:
                        stats["final_audio_seconds"] = round(
                            wav_file.getnframes() / wav_file.getframerate(), 3)
                    stats["wav_bytes"] = processed.stat().st_size
                    processed.replace(destination)
                stats["outcome"] = "ok"
            except Exception as exc:
                stats["error_type"] = type(exc).__name__
                raise SpeechGenerationError(
                    "Generazione WAV Piper/SoX fallita; verifica modello ONNX, "
                    "configurazione JSON ed eseguibile SoX"
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
                processed.unlink(missing_ok=True)
        finally:
            self._generation_lock.release()
            stats["generation_duration_ms"] = round((perf_counter() - generation_started) * 1000, 1)
            stats["cpu_temperature_end_c"] = _cpu_state()["cpu_temperature_c"]
            log_statistics("Speech", stats)

        return filename
