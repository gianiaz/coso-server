"""Short, authenticated snapshot requests to the independent local relay."""

import math
import re
import time
import unicodedata
from http.client import HTTPException
from urllib.parse import urlsplit
from urllib.request import Request

from camera_relay import local_opener
from app.services.openai_service import ImageInput


VISUAL_QUESTION = re.compile(
    r"\b(?:(?:che\s+)?cosa\s+vedi|che\s+(?:oggetto|oggetti)\s+(?:e|sono|vedi)|"
    r"(?:cosa|che\s+cosa)\s+(?:sto|stai)\s+inquadrando|"
    r"(?:descrivi|riconosci|identifica)\s+(?:questo|questa|l[' ]?oggetto|"
    r"quello\s+che\s+vedi)|cosa\s+(?:ho|hai)\s+davanti)\b"
)


def is_visual_question(text):
    normalized = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return bool(VISUAL_QUESTION.search(normalized))


class CameraUnavailable(RuntimeError):
    pass


class CameraService:
    def __init__(self, *, url, api_key, max_age=3.0, max_bytes=512 * 1024, timeout=0.75):
        if url:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
                raise ValueError("CAMERA_RELAY_URL non valido")
        if not math.isfinite(max_age) or not math.isfinite(timeout) or max_age <= 0 or max_bytes <= 0 or timeout <= 0:
            raise ValueError("Limiti camera non validi")
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.max_age = max_age
        self.max_bytes = max_bytes
        self.timeout = timeout

    def snapshot(self):
        if not self.url:
            raise CameraUnavailable("Telecamera non configurata")
        started = time.monotonic()
        try:
            req = Request(self.url + "/snapshot", headers={"X-API-Key": self.api_key})
            with local_opener().open(req, timeout=self.timeout) as response:
                if not 200 <= response.status < 300 or response.headers.get_content_type() != "image/jpeg":
                    raise ValueError("Risposta snapshot non valida")
                age = float(response.headers.get("X-Frame-Age", "nan"))
                data = response.read(self.max_bytes + 1)
                if not math.isfinite(age) or age < 0 or age + time.monotonic() - started > self.max_age:
                    raise ValueError("Fotogramma obsoleto")
                if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9") or len(data) > self.max_bytes:
                    raise ValueError("Fotogramma non valido")
                return ImageInput(data=data, mime_type="image/jpeg")
        except (OSError, ValueError, HTTPException) as exc:
            raise CameraUnavailable("Telecamera non disponibile") from exc
