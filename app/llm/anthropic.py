import anthropic

from app.llm.base import RETRYABLE_STATUS, LLMError, LLMResult, parse_json, retry_after_seconds


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 8000) -> LLMResult:
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APIStatusError as e:
            raise LLMError(
                f"Anthropic API error {e.status_code}: {e.message}",
                retryable=e.status_code in RETRYABLE_STATUS,
                retry_after=retry_after_seconds(e.response.headers.get("retry-after")),
            ) from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Anthropic connection error: {e}", retryable=True) from e

        if response.stop_reason == "refusal":
            raise LLMError("Anthropic declined the request (refusal)")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"Anthropic response was cut off at max_tokens={max_tokens}")

        text = next((b.text for b in response.content if b.type == "text"), "")
        return LLMResult(
            data=parse_json(text),
            model=self.model,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
        )
