"""Patient retries: pause a provider:model after temporary errors, with exponential back-off.

Unscored items simply stay queued (they have no evaluation row) until the pause ends.
"""

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMError, LLMProvider
from app.models import ProviderStatus

BASE_DELAY = 60
MAX_DELAY = 30 * 60


def status_key(provider: LLMProvider | str, model: str | None = None) -> str:
    if isinstance(provider, str):
        return f"{provider}:{model}"
    return f"{provider.name}:{provider.model}"


def backoff_delay(failures: int, retry_after: float | None = None) -> float:
    delay = BASE_DELAY * 2 ** max(failures - 1, 0)
    if retry_after:
        delay = max(retry_after, BASE_DELAY / 2)
    return min(delay, MAX_DELAY) * random.uniform(1.0, 1.2)


async def paused_status(db: AsyncSession, key: str) -> ProviderStatus | None:
    """The status row if this provider:model is currently paused, else None."""
    status = await db.get(ProviderStatus, key)
    if status and status.paused_until and status.paused_until > datetime.now(UTC):
        return status
    return None


async def record_failure(db: AsyncSession, key: str, error: LLMError) -> ProviderStatus:
    status = await db.get(ProviderStatus, key)
    if status is None:
        status = ProviderStatus(key=key, failures=0)
        db.add(status)
    status.failures += 1
    status.paused_until = datetime.now(UTC) + timedelta(seconds=backoff_delay(status.failures, error.retry_after))
    status.last_error = str(error)[:2000]
    return status


async def record_success(db: AsyncSession, key: str) -> None:
    status = await db.get(ProviderStatus, key)
    if status and (status.failures or status.paused_until):
        status.failures = 0
        status.paused_until = None
        status.last_error = None


async def user_llm_status(db: AsyncSession, provider: str, model: str | None, settings) -> ProviderStatus | None:
    """Pause status of the model this user scores with, for display."""
    return await paused_status(db, status_key(provider, model or settings.default_model(provider)))
