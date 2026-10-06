import base64
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ImageInput:
    data: bytes | None = None
    mime_type: str | None = None
    url: str | None = None

    def as_url(self) -> str:
        if self.url:
            return self.url
        if self.data is None or self.mime_type is None:
            raise ValueError("ImageInput incompleto")
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


@dataclass(frozen=True)
class AskResult:
    text: str
    model: str
    response_id: str


class OpenAIServiceError(RuntimeError):
    pass


class OpenAIService:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        instructions: str,
        max_output_tokens: int,
        timeout: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.instructions = instructions
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout

    @staticmethod
    def _extract_text(response: dict) -> str:
        parts = []
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(content["text"])
        return "\n".join(parts)

    def ask(self, *, text: str, image: ImageInput | None = None) -> AskResult:
        prompt = text or "Descrivi questa immagine."
        content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
        if image is not None:
            content.append({"type": "input_image", "image_url": image.as_url()})

        if not self.api_key:
            raise OpenAIServiceError("OPENAI_API_KEY non configurata")

        payload = json.dumps(
            {
                "model": self.model,
                "instructions": self.instructions,
                "input": [{"role": "user", "content": content}],
                "max_output_tokens": self.max_output_tokens,
                "store": False,
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/responses",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as raw_response:
                response = json.load(raw_response)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise OpenAIServiceError("Richiesta OpenAI fallita") from exc

        output_text = self._extract_text(response)
        if not output_text:
            raise OpenAIServiceError("OpenAI ha restituito una risposta vuota")
        return AskResult(
            text=output_text,
            model=response.get("model", self.model),
            response_id=response.get("id", ""),
        )
