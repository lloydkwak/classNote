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

    def _limited_client(self) -> Anthropic:
        """with_backoff does the retrying, so the SDK's own retries would only add delay."""
        return self._client.with_options(
            timeout=self.retry_policy.request_timeout, max_retries=0
        )

    def complete_json(self, prompt: str) -> str:
        response = with_backoff(
            lambda: self._limited_client().messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system="Reply with only valid JSON, with no prose or code fences.",
                messages=[{"role": "user", "content": prompt}],
            ),
            policy=self.retry_policy,
        )
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        self.last_usage = {
            "model": self.model, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None,
        }
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
            lambda: self._limited_client().messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system="Reply with only valid JSON, with no prose or code fences.",
                messages=[{"role": "user", "content": content}],
            ),
            policy=self.retry_policy,
        )
        return "".join(block.text for block in response.content if block.type == "text")
