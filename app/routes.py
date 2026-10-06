import base64
import binascii
import hmac
from dataclasses import dataclass
from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request, send_from_directory, url_for

from app.services.openai_service import ImageInput, OpenAIServiceError
from app.services.speech_service import SpeechGenerationError

api = Blueprint("api", __name__)

HELLO_TEXT = "Ciao, sono Coso, come stai?"

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@dataclass(frozen=True)
class AskInput:
    text: str
    image: ImageInput | None


class ValidationError(ValueError):
    pass


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


@api.get("/health")
def health():
    return jsonify(status="ok", service="coso-server")


@api.get("/hello")
def hello():
    service = current_app.extensions["speech_service"]
    try:
        filename = service.generate(HELLO_TEXT)
    except SpeechGenerationError:
        current_app.logger.exception("Errore durante la generazione del file WAV")
        return jsonify(error="speech_generation_error", message="Impossibile generare l'audio."), 500

    path = url_for("api.wav_file", filename=filename)
    public_base_url = current_app.config["WAV_PUBLIC_BASE_URL"].rstrip("/")
    wav_url = f"{public_base_url}{path}" if public_base_url else url_for(
        "api.wav_file", filename=filename, _external=True
    )
    return jsonify(type="command", wav=wav_url)


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

    service = current_app.extensions["openai_service"]
    try:
        result = service.ask(text=data.text, image=data.image)
    except OpenAIServiceError:
        current_app.logger.exception("Errore durante la richiesta a OpenAI")
        return jsonify(error="upstream_error", message="Il servizio AI non è disponibile."), 502

    return jsonify(answer=result.text, model=result.model, response_id=result.response_id)
