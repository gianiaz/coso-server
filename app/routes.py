import base64
import binascii
import hmac
import random
import wave
from dataclasses import dataclass
from functools import wraps
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request, send_from_directory, url_for

from app.services.openai_service import ImageInput, OpenAIServiceError
from app.services.speech_service import SpeechGenerationError
from app.services.transcription_service import TranscriptionServiceError

api = Blueprint("api", __name__)

HELLO_TEXT = "Ciao, sono Coso, come stai?"

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_AUDIO_TYPES = {"audio/wav", "audio/x-wav"}


@dataclass(frozen=True)
class AskInput:
    text: str
    image: ImageInput | None


class ValidationError(ValueError):
    pass


def _ask_openai(*, text: str, image: ImageInput | None = None):
    memory = current_app.extensions["memory_service"]
    memory_result = memory.process(text)
    memory_context = memory.format_context(memory_result.contexts)
    service = current_app.extensions["openai_service"]
    if memory_context:
        return service.ask(text=text, image=image, memory_context=memory_context)
    return service.ask(text=text, image=image)


def require_api_key(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = current_app.config["COSO_API_KEY"]
        provided = request.headers.get("X-API-Key", "")
        if expected and not hmac.compare_digest(provided, expected):
            return jsonify(error="unauthorized", message="API key mancante o non valida."), 401
        return view(*args, **kwargs)

    return wrapped


def _validate_text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("Il campo 'text' deve essere una stringa.")
    value = value.strip()
    if len(value) > current_app.config["MAX_TEXT_LENGTH"]:
        raise ValidationError("Il testo supera la lunghezza massima consentita.")
    return value


def _validate_image_bytes(data: bytes, mime_type: str) -> ImageInput:
    if mime_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError("Formato immagine non supportato. Usa JPEG, PNG, WEBP o GIF.")
    if not data:
        raise ValidationError("L'immagine è vuota.")
    if len(data) > current_app.config["MAX_IMAGE_BYTES"]:
        raise ValidationError("L'immagine supera la dimensione massima consentita.")
    return ImageInput(data=data, mime_type=mime_type)


def _parse_json() -> AskInput:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("Il body deve essere un oggetto JSON valido.")

    text = _validate_text(body.get("text"))
    image_url = body.get("image_url")
    image_base64 = body.get("image_base64")
    if image_url and image_base64:
        raise ValidationError("Invia una sola immagine: 'image_url' oppure 'image_base64'.")

    image = None
    if image_url:
        if not isinstance(image_url, str):
            raise ValidationError("Il campo 'image_url' deve essere una stringa.")
        parsed = urlparse(image_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError("'image_url' deve essere un URL HTTP o HTTPS valido.")
        image = ImageInput(url=image_url)
    elif image_base64:
        if not isinstance(image_base64, str):
            raise ValidationError("Il campo 'image_base64' deve essere una stringa.")
        mime_type = body.get("image_mime_type", "")
        try:
            raw = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError("'image_base64' non contiene Base64 valido.") from exc
        image = _validate_image_bytes(raw, mime_type)

    return AskInput(text=text, image=image)


def _parse_multipart() -> AskInput:
    text = _validate_text(request.form.get("text"))
    uploaded = request.files.get("image")
    image = None
    if uploaded:
        image = _validate_image_bytes(uploaded.read(), uploaded.mimetype or "")
    return AskInput(text=text, image=image)


def _parse_request() -> AskInput:
    if request.is_json:
        result = _parse_json()
    elif request.mimetype == "multipart/form-data":
        result = _parse_multipart()
    else:
        raise ValidationError("Usa Content-Type application/json oppure multipart/form-data.")

    if not result.text and result.image is None:
        raise ValidationError("Invia almeno 'text' oppure un'immagine.")
    return result


def _parse_audio_question() -> bytes:
    if request.mimetype not in ALLOWED_AUDIO_TYPES:
        raise ValidationError("Usa Content-Type audio/wav.")

    audio = request.get_data(cache=False)
    if not audio:
        raise ValidationError("L'audio è vuoto.")
    if len(audio) > current_app.config["MAX_AUDIO_BYTES"]:
        raise ValidationError("L'audio supera la dimensione massima consentita.")

    try:
        with wave.open(BytesIO(audio), "rb") as wav:
            frame_count = wav.getnframes()
            frames = wav.readframes(frame_count)
            valid = (
                wav.getcomptype() == "NONE"
                and wav.getnchannels() == 1
                and wav.getsampwidth() == 2
                and wav.getframerate() == 16_000
                and frame_count > 1_600
                and len(frames) == frame_count * 2
            )
    except (EOFError, wave.Error):
        valid = False

    if not valid:
        raise ValidationError(
            "Formato audio non valido: usa WAV PCM mono, 16 bit, 16000 Hz "
            "e una durata superiore a 0,1 secondi."
        )
    return audio


def _wav_command(text: str):
    service = current_app.extensions["speech_service"]
    try:
        filename = service.generate(text)
    except SpeechGenerationError:
        current_app.logger.exception("Errore durante la generazione del file WAV")
        return jsonify(error="speech_generation_error", message="Impossibile generare l'audio."), 500

    return _wav_file_command("api.wav_file", filename)


def _wav_file_command(endpoint: str, filename: str):
    path = url_for(endpoint, filename=filename)
    public_base_url = current_app.config["WAV_PUBLIC_BASE_URL"].rstrip("/")
    wav_url = f"{public_base_url}{path}" if public_base_url else url_for(
        endpoint, filename=filename, _external=True
    )
    return jsonify(type="command", wav=wav_url)


@api.get("/health")
def health():
    return jsonify(status="ok", service="coso-server")


@api.get("/hello")
def hello():
    return _wav_command(HELLO_TEXT)


@api.get("/wakeup")
def wakeup():
    directory = Path(current_app.config["WAV_OUTPUT_DIR"]) / "wakeup"
    files = sorted(path.name for path in directory.glob("*.wav") if path.is_file())
    if not files:
        return jsonify(
            error="wakeup_unavailable", message="Nessun saluto WAV disponibile."
        ), 503
    response = _wav_file_command("api.wakeup_wav_file", random.choice(files))
    response.headers["Cache-Control"] = "no-store"
    return response


@api.get("/wav/wakeup/<filename>")
def wakeup_wav_file(filename: str):
    return send_from_directory(
        Path(current_app.config["WAV_OUTPUT_DIR"]) / "wakeup",
        filename,
        mimetype="audio/wav",
        conditional=True,
    )


@api.post("/ask")
def ask_question():
    try:
        audio = _parse_audio_question()
    except ValidationError as exc:
        return jsonify(error="validation_error", message=str(exc)), 400

    transcription_service = current_app.extensions["transcription_service"]
    try:
        question = transcription_service.transcribe(audio)
    except TranscriptionServiceError:
        current_app.logger.exception("Errore durante la trascrizione audio")
        return jsonify(
            error="transcription_error", message="Impossibile trascrivere l'audio."
        ), 502

    try:
        result = _ask_openai(text=question)
    except OpenAIServiceError:
        current_app.logger.exception("Errore durante la richiesta a OpenAI")
        return jsonify(error="upstream_error", message="Il servizio AI non è disponibile."), 502

    return _wav_command(result.text)


@api.get("/wav/<filename>")
def wav_file(filename: str):
    return send_from_directory(
        current_app.config["WAV_OUTPUT_DIR"],
        filename,
        mimetype="audio/wav",
        conditional=True,
    )


@api.post("/api/v1/ask")
@require_api_key
def ask():
    try:
        data = _parse_request()
    except ValidationError as exc:
        return jsonify(error="validation_error", message=str(exc)), 400

    try:
        result = _ask_openai(text=data.text, image=data.image)
    except OpenAIServiceError:
        current_app.logger.exception("Errore durante la richiesta a OpenAI")
        return jsonify(error="upstream_error", message="Il servizio AI non è disponibile."), 502

    return jsonify(answer=result.text, model=result.model, response_id=result.response_id)
