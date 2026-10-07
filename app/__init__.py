import logging
import os
from pathlib import Path

from flask import Flask, jsonify

from app.routes import api
from app.services.openai_service import OpenAIService
from app.services.speech_service import SpeechService


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
        OPENAI_INSTRUCTIONS=os.getenv(
            "OPENAI_INSTRUCTIONS", DEFAULT_OPENAI_INSTRUCTIONS
        ),
        OPENAI_MAX_OUTPUT_TOKENS=_int_env("OPENAI_MAX_OUTPUT_TOKENS", 800),
        OPENAI_TIMEOUT_SECONDS=_int_env("OPENAI_TIMEOUT_SECONDS", 45),
        MAX_CONTENT_LENGTH=_int_env("MAX_REQUEST_BYTES", 8 * 1024 * 1024),
        MAX_IMAGE_BYTES=_int_env("MAX_IMAGE_BYTES", 5 * 1024 * 1024),
        MAX_TEXT_LENGTH=_int_env("MAX_TEXT_LENGTH", 20_000),
        WAV_OUTPUT_DIR=os.getenv(
            "WAV_OUTPUT_DIR",
            str(Path(__file__).resolve().parent.parent / "data" / "wav"),
        ),
        WAV_PUBLIC_BASE_URL=os.getenv("WAV_PUBLIC_BASE_URL", ""),
        ESPEAK_EXECUTABLE=os.getenv("ESPEAK_EXECUTABLE", "espeak-ng"),
        ESPEAK_VOICE=os.getenv("ESPEAK_VOICE", "it"),
        ESPEAK_SPEED=_int_env("ESPEAK_SPEED", 150),
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
    app.extensions["speech_service"] = SpeechService(
        output_dir=Path(app.config["WAV_OUTPUT_DIR"]),
        executable=app.config["ESPEAK_EXECUTABLE"],
        voice=app.config["ESPEAK_VOICE"],
        speed=app.config["ESPEAK_SPEED"],
    )
    app.register_blueprint(api)

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error="request_too_large", message="La richiesta supera il limite configurato."), 413

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify(error="not_found", message="Endpoint non trovato."), 404

    return app
