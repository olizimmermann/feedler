"""Rewrite a user's preference profile from their votes."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.llm.backoff import paused_status, record_failure, record_success, status_key
from app.llm.base import LLMError
from app.llm.prompts import (
    PROFILE_SCHEMA, PROFILE_SYSTEM, InterestSpec, VoteSpec, build_profile_prompt, render_profile,
)
from app.llm.registry import get_provider
from app.models import Evaluation, Interest, Item, PreferenceProfile, User, UserSettings, Vote
from app.pipeline.classify import latest_profile
from app.pipeline.usage import record_call

log = logging.getLogger(__name__)

VOTES_IN_PROMPT = 60


class ProfileError(Exception):
    pass


async def refine_profile(db: AsyncSession, user: User, settings: Settings) -> PreferenceProfile:
    current = await latest_profile(db, user.id)
    if current and current.locked:
        raise ProfileError("The profile is locked. Unlock it to allow automatic refinement.")

    rows = (
        await db.execute(
            select(Vote, Item, Evaluation)
            .join(Item, Item.id == Vote.item_id)
            .outerjoin(Evaluation, (Evaluation.item_id == Vote.item_id) & (Evaluation.user_id == Vote.user_id))
            .where(Vote.user_id == user.id)
            .order_by(Vote.created_at.desc())
            .limit(VOTES_IN_PROMPT)
        )
    ).all()
    if not rows:
        raise ProfileError("Vote on a few items first.")

    votes = [
        VoteSpec(
            value=vote.value,
            title=item.title,
            source=", ".join(s.label for s in item.sources),
            snippet=item.body,
            llm_relevance=ev.relevance if ev else None,
            llm_reason=ev.reason if ev else None,
            note=vote.note,
        )
        for vote, item, ev in rows
    ]
    interests = (await db.scalars(select(Interest).where(Interest.user_id == user.id))).all()
    prompt = build_profile_prompt(
        current.text if current else None,
        [InterestSpec(i.id, i.name, i.description) for i in interests],
        votes,
    )

    us = await db.get(UserSettings, user.id)
    try:
        provider = get_provider(us.llm_provider, us.llm_model, purpose="profile", settings=settings)
    except LLMError as e:
        raise ProfileError(str(e)) from e
    key = status_key(provider)
    if paused := await paused_status(db, key):
        raise ProfileError(
            f"{key} is busy. The profile will be refined automatically after "
            f"{paused.paused_until.strftime('%H:%M')} UTC."
        )
    try:
        result = await provider.complete_json(PROFILE_SYSTEM, prompt, PROFILE_SCHEMA, max_tokens=16000)
    except LLMError as e:
        if e.retryable:
            await record_failure(db, key, e)
        else:
            record_call(db, user.id, provider, "profile", error=e)
        await db.commit()
        raise ProfileError(str(e)) from e
    record_call(db, user.id, provider, "profile", result=result)
    await record_success(db, key)

    text = render_profile(result.data)
    if not text:
        await db.commit()
        raise ProfileError("The model returned an empty profile.")
    profile = PreferenceProfile(
        user_id=user.id, version=(current.version + 1) if current else 1, text=text, origin="auto"
    )
    db.add(profile)
    us.votes_since_refine = 0
    await db.commit()
    log.info("user %s: profile v%d (%s)", user.id, profile.version, result.data.get("changes", ""))
    return profile


async def refine_due(sessionmaker: async_sessionmaker, settings: Settings, min_votes: int | None = None) -> int:
    """Refine profiles of users with at least `min_votes` new votes (default from settings)."""
    threshold = settings.profile_refine_after_votes if min_votes is None else min_votes
    async with sessionmaker() as db:
        user_ids = (
            await db.scalars(select(UserSettings.user_id).where(UserSettings.votes_since_refine >= max(threshold, 1)))
        ).all()
    done = 0
    for user_id in user_ids:
        async with sessionmaker() as db:
            try:
                await refine_profile(db, await db.get(User, user_id), settings)
                done += 1
            except ProfileError as e:
                log.info("user %s: profile not refined: %s", user_id, e)
    return done
