from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.fetchers.base import FetchedItem
from app.llm.registry import set_provider_override
from app.models import (
    Evaluation, Interest, Item, ItemSource, LLMCall, PreferenceProfile, Source, Subscription, User, UserSettings, Vote,
)
from app.pipeline.classify import classify_user
from app.pipeline.ingest import upsert_items
from app.pipeline.profile import ProfileError, refine_due, refine_profile
from tests.fakes import FakeProvider

SETTINGS = Settings(anthropic_api_key="x", item_max_age_days=3)


@pytest.fixture
def fake():
    provider = FakeProvider()
    set_provider_override(lambda name, model: provider)
    yield provider
    set_provider_override(None)


async def make_user(db, email="a@example.com") -> User:
    user = User(email=email, password_hash="x")
    db.add(user)
    await db.flush()
    db.add(UserSettings(user_id=user.id, llm_provider="anthropic"))
    await db.commit()
    return await db.get(User, user.id)


async def make_source(db, kind="reddit", identifier="prusa3d") -> Source:
    source = Source(kind=kind, identifier=identifier, title=identifier)
    db.add(source)
    await db.commit()
    return source


def fetched(ext, url, title="t", body="", hours_ago=1):
    return FetchedItem(external_id=ext, url=url, title=title, body=body,
                       published_at=datetime.now(UTC) - timedelta(hours=hours_ago))


async def test_upsert_dedupes_by_external_id_and_canonical_url(db):
    reddit = await make_source(db)
    rss = await make_source(db, "rss", "https://blog.example.com/feed")

    new = await upsert_items(db, reddit, [
        fetched("reddit:t3_1", "https://blog.example.com/fix/?utm_source=reddit"),
        fetched("reddit:t3_2", "https://www.reddit.com/r/prusa3d/comments/2/"),
    ])
    assert new == 2
    # Same article from the blog's own feed, plus a re-fetch of a known reddit post
    assert await upsert_items(db, rss, [fetched("rss:aaa", "https://blog.example.com/fix")]) == 0
    assert await upsert_items(db, reddit, [fetched("reddit:t3_2", "https://www.reddit.com/r/prusa3d/comments/2/")]) == 0
    await db.commit()

    assert await db.scalar(select(func.count(Item.id))) == 2
    merged = await db.scalar(select(Item).where(Item.external_id == "reddit:t3_1"))
    links = (await db.scalars(select(ItemSource.source_id).where(ItemSource.item_id == merged.id))).all()
    assert sorted(links) == sorted([reddit.id, rss.id])


async def setup_feed(db, n_items=3):
    user = await make_user(db)
    source = await make_source(db)
    db.add(Subscription(user_id=user.id, source_id=source.id))
    db.add(Interest(id=1, user_id=user.id, name="Bugs", description="printer bugs"))
    await db.commit()
    items = [fetched(f"reddit:t3_{i}", f"https://r.com/{i}", body="a bug" if i % 2 == 0 else "a benchy")
             for i in range(n_items)]
    items.append(fetched("reddit:t3_old", "https://r.com/old", body="bug", hours_ago=24 * 10))
    await upsert_items(db, source, items)
    await db.commit()
    return user


async def test_classify_scores_and_clamps(db, fake):
    user = await setup_feed(db)
    assert await classify_user(db, user, SETTINGS) == 3  # the 10-day-old item is skipped

    evs = {e.item_id: e for e in (await db.scalars(select(Evaluation))).all()}
    assert len(evs) == 3
    scores = sorted(e.relevance for e in evs.values())
    assert scores == [10, 100, 100]
    hit = next(e for e in evs.values() if e.relevance == 100)
    assert hit.matched_interest_ids == [1]  # unknown interest 999 dropped
    assert hit.model == "anthropic:fake-model"

    call = await db.scalar(select(LLMCall))
    assert call.ok and call.tokens_in == 1000 and call.purpose == "classify"
    # Second run: nothing left to score, no extra LLM call
    assert await classify_user(db, user, SETTINGS) == 0
    assert len(fake.prompts) == 1


async def test_classify_missing_result_and_failure(db, fake):
    user = await setup_feed(db, n_items=2)
    fake.fail = True
    assert await classify_user(db, user, SETTINGS) == 0
    assert await db.scalar(select(func.count(Evaluation.id))) == 0
    failed = await db.scalar(select(LLMCall))
    assert not failed.ok and "boom" in failed.error

    fake.fail = False
    first_id = await db.scalar(select(Item.id).where(Item.external_id == "reddit:t3_0"))
    fake.drop_ids = {first_id}
    assert await classify_user(db, user, SETTINGS) == 2
    dropped = await db.scalar(select(Evaluation).where(Evaluation.item_id == first_id))
    assert dropped.relevance == 0 and "no score" in dropped.reason


async def test_classify_includes_comments_only_when_wanted(db, fake):
    user = await setup_feed(db, n_items=1)
    item = await db.scalar(select(Item).where(Item.external_id == "reddit:t3_0"))
    item.comments_text = "[+5] u/x: FIXED by updating firmware"
    interest = await db.get(Interest, 1)
    interest.include_comments = False
    await db.commit()
    await classify_user(db, user, SETTINGS)
    assert "FIXED" not in fake.prompts[0]


async def test_profile_refinement(db, fake):
    user = await setup_feed(db, n_items=2)
    with pytest.raises(ProfileError):
        await refine_profile(db, user, SETTINGS)  # no votes yet

    await classify_user(db, user, SETTINGS)
    for item in (await db.scalars(select(Item))).all():
        db.add(Vote(user_id=user.id, item_id=item.id, value=1, note="more of this"))
    (await db.get(UserSettings, user.id)).votes_since_refine = 10
    await db.commit()

    profile = await refine_profile(db, user, SETTINGS)
    assert profile.version == 1 and profile.origin == "auto"
    assert profile.text == "Wants:\n- bug fixes\n\nDoesn't want:\n- showcases"
    assert (await db.get(UserSettings, user.id)).votes_since_refine == 0
    assert "more of this" in fake.prompts[-1] and '"filter_score"' in fake.prompts[-1]

    profile.locked = True
    await db.commit()
    with pytest.raises(ProfileError):
        await refine_profile(db, user, SETTINGS)


async def test_refine_due_threshold(sessionmaker, fake):
    async with sessionmaker() as db:
        user = await setup_feed(db, n_items=1)
        item_id = await db.scalar(select(Item.id).limit(1))
        db.add(Vote(user_id=user.id, item_id=item_id, value=-1))
        (await db.get(UserSettings, user.id)).votes_since_refine = 3
        await db.commit()

    assert await refine_due(sessionmaker, Settings(profile_refine_after_votes=10)) == 0
    assert await refine_due(sessionmaker, Settings(profile_refine_after_votes=10), min_votes=1) == 1
    async with sessionmaker() as db:
        assert await db.scalar(select(func.count(PreferenceProfile.id))) == 1


async def test_overload_pauses_and_keeps_queue(db, fake):
    from app.llm.backoff import paused_status
    from app.models import ProviderStatus
    from app.pipeline.classify import queue_filter

    user = await setup_feed(db)
    fake.overloaded = True
    assert await classify_user(db, user, SETTINGS) == 0
    status = await paused_status(db, "anthropic:fake-model")
    assert status.failures == 1 and "high demand" in status.last_error
    # A temporary error is not a failed call, and nothing is lost from the queue
    assert await db.scalar(select(func.count(LLMCall.id))) == 0
    assert await db.scalar(select(func.count(Item.id)).where(*queue_filter(user.id, SETTINGS))) == 3

    # While paused, no request is sent at all
    calls = len(fake.prompts)
    fake.overloaded = False
    assert await classify_user(db, user, SETTINGS) == 0
    assert len(fake.prompts) == calls

    # Pause over: the queue drains and the back-off resets
    status.paused_until = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    assert await classify_user(db, user, SETTINGS) == 3
    status = await db.get(ProviderStatus, "anthropic:fake-model")
    await db.refresh(status)
    assert status.failures == 0 and status.paused_until is None


def test_backoff_grows_and_caps():
    from app.llm.backoff import MAX_DELAY, backoff_delay

    assert 60 <= backoff_delay(1) <= 72
    assert 120 <= backoff_delay(2) <= 144
    assert MAX_DELAY <= backoff_delay(20) <= MAX_DELAY * 1.2
    assert 30 <= backoff_delay(1, retry_after=5) <= 36  # tiny hints are floored
    assert 200 <= backoff_delay(1, retry_after=200) <= 240


async def test_queue_keeps_items_fetched_after_subscribing(db, fake):
    from app.pipeline.classify import queue_filter

    user = await setup_feed(db, n_items=0)
    # Fetched 5 days ago while subscribed (e.g. during an outage), published shortly before
    item = await db.scalar(select(Item))
    item.fetched_at = datetime.now(UTC) - timedelta(days=5)
    item.published_at = datetime.now(UTC) - timedelta(days=5, hours=1)
    sub = await db.scalar(select(Subscription))
    sub.created_at = datetime.now(UTC) - timedelta(days=6)
    await db.commit()
    assert await db.scalar(select(func.count(Item.id)).where(*queue_filter(user.id, SETTINGS))) == 1

    # A user who subscribed after it was fetched doesn't get this old backfill
    sub.created_at = datetime.now(UTC) - timedelta(days=1)
    await db.commit()
    assert await db.scalar(select(func.count(Item.id)).where(*queue_filter(user.id, SETTINGS))) == 0


@pytest.fixture
def fakes():
    """A separate fake per provider name, so a busy primary can hand over to a fallback."""
    by_name: dict[str, FakeProvider] = {}

    def factory(name, model):
        return by_name.setdefault(name, FakeProvider(name=name, model=f"{name}-model"))

    set_provider_override(factory)
    yield factory
    set_provider_override(None)


async def test_busy_provider_falls_back(db, fakes):
    from app.llm.backoff import paused_status, user_llm_status
    from app.pipeline.classify import queue_filter

    user = await setup_feed(db)
    us = await db.get(UserSettings, user.id)
    us.llm_provider, us.llm_fallbacks = "gemini", ["ollama"]
    await db.commit()
    gemini, ollama = fakes("gemini", None), fakes("ollama", None)
    gemini.overloaded = True

    # The batch gemini turned away is scored by ollama in the same run
    assert await classify_user(db, user, SETTINGS) == 3
    models = set((await db.scalars(select(Evaluation.model))).all())
    assert models == {"ollama:ollama-model"}
    assert (await paused_status(db, "gemini:gemini-model")).failures == 1
    assert await db.scalar(select(func.count(LLMCall.id)).where(LLMCall.ok.is_(False))) == 0
    status = await user_llm_status(db, us, SETTINGS)
    assert status.paused.key == "gemini:gemini-model" and status.active == "ollama:ollama-model"
    assert status.resumes is None

    # While gemini is paused it isn't asked at all, and refinement uses ollama too
    gemini.prompts.clear()
    item_id = await db.scalar(select(Item.id).limit(1))
    db.add(Vote(user_id=user.id, item_id=item_id, value=1))
    await db.commit()
    assert (await refine_profile(db, user, SETTINGS)).version == 1
    assert gemini.prompts == [] and len(ollama.prompts) == 2

    # Everything busy: items stay queued and the status says when the next try is
    ollama.overloaded = True
    await db.execute(Evaluation.__table__.delete())
    await db.commit()
    assert await classify_user(db, user, SETTINGS) == 0
    assert await db.scalar(select(func.count(Item.id)).where(*queue_filter(user.id, SETTINGS))) == 3
    status = await user_llm_status(db, us, SETTINGS)
    assert status.active is None and status.resumes is not None
    with pytest.raises(ProfileError, match="are busy"):
        await refine_profile(db, user, SETTINGS)


def test_fallback_defaults():
    s = Settings(default_llm_provider="gemini", default_llm_fallbacks=" Ollama, gemini, nope, ollama")
    assert s.fallback_providers == ["ollama"]
