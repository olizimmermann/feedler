import json
import re
from dataclasses import dataclass
from typing import Protocol


class LLMError(Exception):
    """A failed LLM call. `retryable` marks temporary trouble (overload, rate limit, network)
    that should pause and retry later rather than count as a real failure."""

    def __init__(self, message: str, *, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def retry_after_seconds(value) -> float | None:
    """Parse a Retry-After header or a Google-style retryDelay ("30s")."""
    if value is None:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return float(match.group(1)) if match else None


@dataclass
class LLMResult:
    data: dict
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = 8000) -> LLMResult:
        """Run one prompt and return JSON that follows `schema`."""
        ...


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> dict:
    """Lenient JSON parse for providers without guaranteed structured output."""
    text = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError(f"Model did not return valid JSON: {text[:300]!r}")
