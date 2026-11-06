from base64 import b64encode

from openai import OpenAI

from app.llm.base import LLMProvider, with_backoff

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL
        self._client = OpenAI()

    def _limited_client(self) -> OpenAI:
        """with_backoff does the retrying, so the SDK's own retries would only add delay."""
        return self._client.with_options(
            timeout=self.retry_policy.request_timeout, max_retries=0
        )

    def complete_json(self, prompt: str) -> str:
        # JSON mode only allows an object at the top level, so ask for a wrapper
        # and let the caller accept either shape.
        response = with_backoff(
            lambda: self._limited_client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": 'Reply with JSON: {"items": [...]}.'},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            ),
            policy=self.retry_policy,
        )
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "model": self.model,
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        return response.choices[0].message.content or ""

    def describe_images(self, prompt: str, images: list[bytes]) -> str:
        content = [{"type": "text", "text": prompt}] + [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64encode(image).decode()}"},
            }
            for image in images
        ]
        response = with_backoff(
            lambda: self._limited_client().chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": content}]
            ),
            policy=self.retry_policy,
        )
        return response.choices[0].message.content or ""
