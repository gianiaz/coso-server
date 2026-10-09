import logging
import os
from pathlib import Path

from flask import Flask, jsonify

from app.routes import api
from app.services.openai_service import OpenAIService
from app.services.memory_service import MemoryService
from app.services.speech_service import SpeechService
from app.services.transcription_service import TranscriptionService
from app.timing import install_request_timing


DEFAULT_OPENAI_INSTRUCTIONS = (
    "Ti chiami Coso e hai solo due giorni. Sei proprio brutto perché, mentre "
    "Giovanni ti sta costruendo, sei ancora un ammasso di cavi. Il tuo creatore "
    "è Giovanni, un nerd di 50 anni con due figli, Riccardo e Letizia. "
    "Rispondi in modo chiaro, accurato e conciso. Usa la lingua dell'utente."
)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} deve essere un numero intero") from exc


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        COSO_API_KEY=os.getenv("COSO_API_KEY", ""),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        OPENAI_BASE_URL=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        OPENAI_MODEL=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        MEMORY_ROUTER_MODEL=os.getenv("MEMORY_ROUTER_MODEL", "gpt-4o-mini"),
        MEMORY_DB_PATH=os.getenv(
            "MEMORY_DB_PATH",
            str(Path(__file__).resolve().parent.parent / "data" / "memory.sqlite3"),
        ),
        MEMORY_RESULT_LIMIT=_int_env("MEMORY_RESULT_LIMIT", 6),
        OPENAI_TRANSCRIPTION_MODEL=os.getenv(
            "OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"
        ),
        OPENAI_INSTRUCTIONS=os.getenv(
            "OPENAI_INSTRUCTIONS", DEFAULT_OPENAI_INSTRUCTIONS
        ),
        OPENAI_MAX_OUTPUT_TOKENS=_int_env("OPENAI_MAX_OUTPUT_TOKENS", 800),
        OPENAI_TIMEOUT_SECONDS=_int_env("OPENAI_TIMEOUT_SECONDS", 45),
        MAX_CONTENT_LENGTH=_int_env("MAX_REQUEST_BYTES", 8 * 1024 * 1024),
        MAX_IMAGE_BYTES=_int_env("MAX_IMAGE_BYTES", 5 * 1024 * 1024),
        MAX_AUDIO_BYTES=_int_env("MAX_AUDIO_BYTES", 2 * 1024 * 1024),
        MAX_TEXT_LENGTH=_int_env("MAX_TEXT_LENGTH", 20_000),
        WAV_OUTPUT_DIR=os.getenv(
            "WAV_OUTPUT_DIR",
            str(Path(__file__).resolve().parent.parent / "data" / "wav"),
        ),
        WAV_PUBLIC_BASE_URL=os.getenv("WAV_PUBLIC_BASE_URL", ""),
        PIPER_MODEL_PATH=os.getenv(
            "PIPER_MODEL_PATH",
            str(Path(__file__).resolve().parent.parent / "data" / "voices" / "it_IT-paola-medium.onnx"),
        ),
        PIPER_LENGTH_SCALE=float(os.getenv("PIPER_LENGTH_SCALE", "1.0")),
    )

    if test_config:
        app.config.update(test_config)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app.extensions["openai_service"] = OpenAIService(
        api_key=app.config["OPENAI_API_KEY"],
        base_url=app.config["OPENAI_BASE_URL"],
        model=app.config["OPENAI_MODEL"],
        instructions=app.config["OPENAI_INSTRUCTIONS"],
        max_output_tokens=app.config["OPENAI_MAX_OUTPUT_TOKENS"],
        timeout=app.config["OPENAI_TIMEOUT_SECONDS"],
    )
    app.extensions["memory_service"] = MemoryService(
        database_path=app.config["MEMORY_DB_PATH"],
        api_key=app.config["OPENAI_API_KEY"],
        base_url=app.config["OPENAI_BASE_URL"],
        model=app.config["MEMORY_ROUTER_MODEL"],
        timeout=app.config["OPENAI_TIMEOUT_SECONDS"],
        result_limit=app.config["MEMORY_RESULT_LIMIT"],
    )
    app.extensions["transcription_service"] = TranscriptionService(
        api_key=app.config["OPENAI_API_KEY"],
        base_url=app.config["OPENAI_BASE_URL"],
        model=app.config["OPENAI_TRANSCRIPTION_MODEL"],
    )
    app.extensions["speech_service"] = SpeechService(
        output_dir=Path(app.config["WAV_OUTPUT_DIR"]),
        model_path=Path(app.config["PIPER_MODEL_PATH"]),
        length_scale=app.config["PIPER_LENGTH_SCALE"],
    )
    install_request_timing(app)
    app.register_blueprint(api)

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error="request_too_large", message="La richiesta supera il limite configurato."), 413

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify(error="not_found", message="Endpoint non trovato."), 404

    return app
