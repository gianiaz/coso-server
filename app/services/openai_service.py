import base64
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


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
        self.model = model
        self.instructions = instructions
        self._chat_model = (
            ChatOpenAI(
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                model=model,
                max_tokens=max_output_tokens,
                timeout=timeout,
                max_retries=0,
                store=False,
                use_responses_api=True,
                output_version="responses/v1",
            )
            if api_key
            else None
        )

    @staticmethod
    def _extract_text(response: AIMessage) -> str:
        if isinstance(response.content, str):
            return response.content

        parts: list[str] = []
        for block in response.content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                if text := block.get("text"):
                    parts.append(text)
        return "\n".join(parts)

    def ask(
        self,
        *,
        text: str,
        image: ImageInput | None = None,
        memory_context: str = "",
    ) -> AskResult:
        prompt = text or "Descrivi questa immagine."
        content: list[dict] = [{"type": "text", "text": prompt}]
        if image is not None:
            content.append(
                {"type": "image_url", "image_url": {"url": image.as_url()}}
            )

        if self._chat_model is None:
            raise OpenAIServiceError("OPENAI_API_KEY non configurata")

        try:
            messages = [SystemMessage(content=self.instructions)]
            if memory_context:
                messages.append(SystemMessage(content=memory_context))
            messages.append(HumanMessage(content=content))
            response = self._chat_model.invoke(messages)
        except Exception as exc:
            raise OpenAIServiceError("Richiesta OpenAI fallita") from exc

        output_text = self._extract_text(response)
        if not output_text:
            raise OpenAIServiceError("OpenAI ha restituito una risposta vuota")
        return AskResult(
            text=output_text,
            model=response.response_metadata.get("model_name", self.model),
            response_id=response.response_metadata.get("id", ""),
        )
