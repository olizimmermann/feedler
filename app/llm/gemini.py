import re

import httpx
from google import genai
from google.genai import errors, types

from app.llm.base import RETRYABLE_STATUS, LLMError, LLMResult, parse_json, retry_after_seconds

_GOOGLE_RETRY_RE = re.compile(r"'retryDelay': '([\d.]+)s'")


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = genai.Client(api_key=api_key)

    async def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 8000) -> LLMResult:
        try:
            resp = await self.client.aio.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    max_output_tokens=max_tokens,
                ),
            )
        except errors.APIError as e:
            delay = _GOOGLE_RETRY_RE.search(str(e.details)) if e.details else None
            raise LLMError(
                f"Gemini API error {e.code}: {e.message}",
                retryable=e.code in RETRYABLE_STATUS,
                retry_after=retry_after_seconds(delay.group(1)) if delay else None,
            ) from e
        except httpx.TransportError as e:
            raise LLMError(f"Gemini connection error: {e}", retryable=True) from e

        if not resp.text:
            raise LLMError("Gemini returned an empty response")
        usage = resp.usage_metadata
        return LLMResult(
            data=parse_json(resp.text),
            model=self.model,
            tokens_in=(usage.prompt_token_count or 0) if usage else 0,
            tokens_out=(usage.candidates_token_count or 0) if usage else 0,
        )
