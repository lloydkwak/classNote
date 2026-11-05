import os

from google import genai
from google.genai import types

from app.llm.base import LLMProvider, with_backoff

DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env.")
        self.model = model or DEFAULT_MODEL
        self._client = genai.Client(api_key=api_key)

    def complete_json(self, prompt: str) -> str:
        response = with_backoff(
            lambda: self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
        )
        return response.text or ""
