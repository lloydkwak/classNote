from base64 import b64encode

from anthropic import Anthropic

from app.llm.base import LLMProvider, with_backoff

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 4096


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL
        self._client = Anthropic()

    def complete_json(self, prompt: str) -> str:
        response = with_backoff(
            lambda: self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system="Reply with only valid JSON, with no prose or code fences.",
                messages=[{"role": "user", "content": prompt}],
            )
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def describe_images(self, prompt: str, images: list[bytes]) -> str:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64encode(image).decode(),
                },
            }
            for image in images
        ] + [{"type": "text", "text": prompt}]
        response = with_backoff(
            lambda: self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system="Reply with only valid JSON, with no prose or code fences.",
                messages=[{"role": "user", "content": content}],
            )
        )
        return "".join(block.text for block in response.content if block.type == "text")
