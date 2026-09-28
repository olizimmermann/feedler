"""Score unevaluated items for each user with their chosen LLM."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.llm.backoff import paused_status, record_failure, record_success, status_key
from app.llm.base import LLMError
from app.llm.prompts import CLASSIFY_SCHEMA, CLASSIFY_SYSTEM, InterestSpec, PostSpec, build_classify_prompt
from app.llm.registry import get_provider
from app.models import (
    Evaluation, Interest, Item, ItemSource, PreferenceProfile, Source, Subscription, User, UserSettings,
)
from app.pipeline.usage import record_call

log = logging.getLogger(__name__)

MAX_BATCHES_PER_RUN = 5


async def classify_all(sessionmaker: async_sessionmaker, settings: Settings) -> int:
    async with sessionmaker() as db:
        user_ids = (
            await db.scalars(
                select(User.id)
                .where(exists().where(Interest.user_id == User.id, Interest.enabled.is_(True)))
                .where(exists().where(Subscription.user_id == User.id))
            )
        ).all()
    total = 0
    for user_id in user_ids:
        async with sessionmaker() as db:
            total += await classify_user(db, await db.get(User, user_id), settings)
    return total


QUEUE_MAX_DAYS = 14


def queue_filter(user_id: int, settings: Settings) -> list:
    """Conditions for items waiting to be scored for a user.

    Posts fetched after the user subscribed stay queued (up to QUEUE_MAX_DAYS), so an LLM
    outage delays scoring but loses nothing. Posts that already existed when the user
    subscribed are only backfilled if they are recent (item_max_age_days).
    """
    now = datetime.now(UTC)
    recent = Item.published_at >= now - timedelta(days=settings.item_max_age_days)
    fetched_while_subscribed = and_(
        Item.fetched_at >= now - timedelta(days=QUEUE_MAX_DAYS),
        Item.published_at >= Item.fetched_at - timedelta(days=settings.item_max_age_days),
        exists().where(
            ItemSource.item_id == Item.id,
            Subscription.source_id == ItemSource.source_id,
            Subscription.user_id == user_id,
            Subscription.created_at <= Item.fetched_at,
        ),
    )
    subscribed = select(Subscription.source_id).where(Subscription.user_id == user_id)
    return [
        exists().where(ItemSource.item_id == Item.id, ItemSource.source_id.in_(subscribed)),
        or_(recent, fetched_while_subscribed),
        ~exists().where(Evaluation.item_id == Item.id, Evaluation.user_id == user_id),
    ]


async def latest_profile(db: AsyncSession, user_id: int) -> PreferenceProfile | None:
    return await db.scalar(
        select(PreferenceProfile)
        .where(PreferenceProfile.user_id == user_id)
        .order_by(PreferenceProfile.version.desc())
        .limit(1)
    )


async def classify_user(
    db: AsyncSession, user: User, settings: Settings, max_batches: int = MAX_BATCHES_PER_RUN
) -> int:
    us = await db.get(UserSettings, user.id)
    try:
        provider = get_provider(us.llm_provider, us.llm_model, settings=settings)
    except LLMError as e:
        log.warning("user %s: %s", user.id, e)
        return 0

    key = status_key(provider)
    if paused := await paused_status(db, key):
        log.debug("user %s: %s paused until %s", user.id, key, paused.paused_until)
        return 0

    interests = (
        await db.scalars(select(Interest).where(Interest.user_id == user.id, Interest.enabled.is_(True)))
    ).all()
    if not interests:
        return 0
    profile = await latest_profile(db, user.id)

    batch_size = settings.batch_size(provider.name)
    subscribed = select(Subscription.source_id).where(Subscription.user_id == user.id)
    items = (
        await db.scalars(
            select(Item)
            .where(*queue_filter(user.id, settings))
            .order_by(Item.published_at.desc())
            .limit(batch_size * max_batches)
        )
    ).all()
    if not items:
        return 0

    sub_source_ids = set((await db.scalars(subscribed)).all())
    interest_specs = [
        InterestSpec(i.id, i.name, i.description, i.source.label if i.source else None) for i in interests
    ]
    done = 0
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        posts = [_post_spec(item, interests, sub_source_ids) for item in batch]
        prompt = build_classify_prompt(
            interest_specs, profile.text if profile else None, posts, settings.item_char_budget(provider.name)
        )
        try:
            result = await provider.complete_json(
                CLASSIFY_SYSTEM, prompt, CLASSIFY_SCHEMA, max_tokens=600 + 250 * len(batch)
            )
        except LLMError as e:
            if e.retryable:
                # Temporary trouble: pause this model and leave the items queued
                status = await record_failure(db, key, e)
                log.info("%s unavailable (attempt %d), retrying after %s: %s",
                         key, status.failures, status.paused_until.strftime("%H:%M:%S"), e)
            else:
                record_call(db, user.id, provider, "classify", error=e)
                log.warning("classification failed for user %s: %s", user.id, e)
            await db.commit()
            break
        record_call(db, user.id, provider, "classify", result=result)
        await record_success(db, key)

        valid_interest_ids = {i.id for i in interests}
        by_id = {r.get("id"): r for r in result.data.get("results", []) if isinstance(r, dict)}
        rows = []
        for item in batch:
            r = by_id.get(item.id)
            if r is None:
                rows.append(dict(relevance=0, matched_interest_ids=[], reason="The model returned no score for this post.", summary=""))
            else:
                rows.append(
                    dict(
                        relevance=max(0, min(100, int(r.get("relevance", 0)))),
                        matched_interest_ids=[i for i in r.get("matched_interests", []) if i in valid_interest_ids],
                        reason=str(r.get("reason", ""))[:2000],
                        summary=str(r.get("summary", ""))[:4000],
                    )
                )
            rows[-1].update(
                item_id=item.id,
                user_id=user.id,
                model=f"{provider.name}:{provider.model}",
                profile_version=profile.version if profile else 0,
            )
        await db.execute(pg_insert(Evaluation).values(rows).on_conflict_do_nothing())
        await db.commit()
        done += len(batch)
    log.info("user %s: classified %d items", user.id, done)
    return done


def _post_spec(item: Item, interests: list[Interest], sub_source_ids: set[int]) -> PostSpec:
    sources: list[Source] = [s for s in item.sources if s.id in sub_source_ids] or list(item.sources)
    source_ids = {s.id for s in sources}
    wants_comments = any(
        i.include_comments and (i.source_id is None or i.source_id in source_ids) for i in interests
    )
    return PostSpec(
        id=item.id,
        source=", ".join(s.label for s in sources),
        title=item.title,
        body=item.body,
        comments=item.comments_text if wants_comments else "",
    )
