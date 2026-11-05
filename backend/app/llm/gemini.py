import logging
import os

from google import genai
from google.genai import types

from app.llm.base import LLMProvider, QuotaExhausted, with_backoff

log = logging.getLogger(__name__)

# The free tier allows only about 20 requests per day, per model, so a lecture can use up
# several models. Newer models were also often overloaded (503) when this list was chosen.
DEFAULT_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
REQUEST_TIMEOUT_MS = 120_000


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env.")
        self._models = [model] if model else list(DEFAULT_MODELS)
        # The cache key, which stays the same when a call falls back to another model.
        self.model = self._models[0]
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS)
        )

    def _generate(self, contents: list) -> str:
        config = types.GenerateContentConfig(response_mime_type="application/json")
        while True:
            model = self._models[0]
            try:
                response = with_backoff(
                    lambda: self._client.models.generate_content(
                        model=model, contents=contents, config=config
                    )
                )
                return response.text or ""
            except QuotaExhausted:
                if len(self._models) == 1:
                    raise QuotaExhausted(
                        "The Gemini free-tier daily limit is used up. Finished work is cached, so "
                        "run this again tomorrow, or set a paid key."
                    ) from None
                log.warning("Daily limit reached on %s; trying the next model.", model)
                self._models.pop(0)

    def complete_json(self, prompt: str) -> str:
        return self._generate([prompt])

    def describe_images(self, prompt: str, images: list[bytes]) -> str:
        parts = [types.Part.from_bytes(data=image, mime_type="image/png") for image in images]
        return self._generate([*parts, prompt])
