import openai

from app.llm.base import RETRYABLE_STATUS, LLMError, LLMResult, parse_json, retry_after_seconds
from app.llm.ollama_pull import pull_in_background


class OpenAICompatProvider:
    """OpenAI Chat Completions. Also serves Ollama through its OpenAI-compatible API."""

    def __init__(self, name: str, model: str, api_key: str, base_url: str | None = None):
        self.name = name
        self.model = model
        self.base_url = base_url or ""
        self.client = openai.AsyncOpenAI(api_key=api_key or "unused", base_url=base_url)

    async def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 8000) -> LLMResult:
        token_arg = {"max_tokens": max_tokens} if self.name == "ollama" else {"max_completion_tokens": max_tokens}
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "result", "schema": schema, "strict": True},
                },
                **token_arg,
            )
        except openai.APIStatusError as e:
            if self.name == "ollama" and e.status_code == 404 and "not found" in str(e.message):
                pull_in_background(self.base_url, self.model)
                raise LLMError(
                    f"Ollama model {self.model} isn't downloaded yet. It's downloading now, "
                    "and posts will be scored once it's ready.",
                    retryable=True,
                    retry_after=60,
                ) from e
            raise LLMError(
                f"{self.name} API error {e.status_code}: {e.message}",
                retryable=e.status_code in RETRYABLE_STATUS,
                retry_after=retry_after_seconds(e.response.headers.get("retry-after")),
            ) from e
        except openai.APIConnectionError as e:
            raise LLMError(f"{self.name} connection error: {e}", retryable=True) from e

        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(f"{self.name} response was cut off at max_tokens={max_tokens}")
        if getattr(choice.message, "refusal", None):
            raise LLMError(f"{self.name} refused: {choice.message.refusal}")
        usage = resp.usage
        return LLMResult(
            data=parse_json(choice.message.content or ""),
            model=self.model,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
        )
