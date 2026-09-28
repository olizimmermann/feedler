from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMProvider, LLMResult
from app.models import LLMCall


def record_call(
    db: AsyncSession,
    user_id: int | None,
    provider: LLMProvider,
    purpose: str,
    result: LLMResult | None = None,
    error: Exception | None = None,
) -> None:
    db.add(
        LLMCall(
            user_id=user_id,
            provider=provider.name,
            model=provider.model,
            purpose=purpose,
            tokens_in=result.tokens_in if result else 0,
            tokens_out=result.tokens_out if result else 0,
            ok=error is None,
            error=str(error)[:2000] if error else None,
        )
    )
