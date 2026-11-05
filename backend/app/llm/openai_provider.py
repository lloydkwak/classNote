from base64 import b64encode

from openai import OpenAI

from app.llm.base import LLMProvider, with_backoff

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL
        self._client = OpenAI()

    def complete_json(self, prompt: str) -> str:
        # JSON mode only allows an object at the top level, so ask for a wrapper
        # and let the caller accept either shape.
        response = with_backoff(
            lambda: self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": 'Reply with JSON: {"items": [...]}.'},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
        )
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
            lambda: self._client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": content}]
            )
        )
        return response.choices[0].message.content or ""
