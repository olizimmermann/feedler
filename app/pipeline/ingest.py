"""Poll due sources and upsert their items."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.fetchers.base import FetchedItem, canonicalize_url
from app.fetchers.reddit import RedditFetcher
from app.fetchers.rss import RSSFetcher
from app.models import Evaluation, Interest, Item, ItemSource, Source, Subscription, UserSettings

log = logging.getLogger(__name__)

# Reddit comments with fixes tend to arrive after the post; re-read them at these ages
COMMENT_REFRESH_AGES = [timedelta(hours=2), timedelta(hours=12)]
COMMENT_REFRESH_MIN_RELEVANCE = 50
COMMENT_REFRESH_BATCH = 30


@dataclass
class Fetchers:
    reddit: RedditFetcher
    rss: RSSFetcher

    @classmethod
    def create(cls, settings: Settings) -> "Fetchers":
        return cls(reddit=RedditFetcher(settings), rss=RSSFetcher(settings.reddit_user_agent))

    async def aclose(self) -> None:
        await self.reddit.aclose()
        await self.rss.aclose()


async def poll_due_sources(sessionmaker: async_sessionmaker, fetchers: Fetchers, settings: Settings) -> int:
    now = datetime.now(UTC)
    async with sessionmaker() as db:
        due = (
            await db.scalars(
                select(Source)
                .where(exists().where(Subscription.source_id == Source.id))
                .where(
                    or_(
                        Source.last_polled_at.is_(None),
                        Source.last_polled_at
                        < now - func.make_interval(0, 0, 0, 0, 0, Source.poll_interval_minutes),
                    )
                )
                .order_by(Source.last_polled_at.asc().nulls_first())
            )
        ).all()
        source_ids = [s.id for s in due]

    total_new = 0
    for source_id in source_ids:
        async with sessionmaker() as db:
            source = await db.get(Source, source_id)
            total_new += await poll_source(db, source, fetchers, settings)
    return total_new


async def comment_options(db: AsyncSession, source: Source) -> tuple[bool, int]:
    """Whether any subscriber wants comments for this source, and how many."""
    subscribers = select(Subscription.user_id).where(Subscription.source_id == source.id)
    wants = await db.scalar(
        select(
            exists().where(
                Interest.user_id.in_(subscribers),
                Interest.enabled.is_(True),
                Interest.include_comments.is_(True),
                or_(Interest.source_id.is_(None), Interest.source_id == source.id),
            )
        )
    )
    top_n = await db.scalar(select(func.max(UserSettings.comments_top_n)).where(UserSettings.user_id.in_(subscribers)))
    return bool(wants), int(top_n or 10)


async def poll_source(db: AsyncSession, source: Source, fetchers: Fetchers, settings: Settings) -> int:
    now = datetime.now(UTC)
    source_id, label = source.id, source.label
    try:
        if source.kind == "reddit":
            result = await fetchers.reddit.fetch(source.identifier)
            if fetchers.reddit.oauth:
                with_comments, top_n = await comment_options(db, source)
                if with_comments:
                    known = await _known_ids(db, [i.external_id for i in result.items])
                    for item in result.items:
                        if item.external_id not in known and item.num_comments:
                            await _attach_comments(fetchers.reddit, item, top_n)
        else:
            known = await _known_ids(db, None, source_id=source.id)
            result = await fetchers.rss.fetch(
                source.identifier, etag=source.etag, last_modified=source.last_modified, known_ids=known
            )
            source.etag, source.last_modified = result.etag, result.last_modified
            if result.title and source.title == source.identifier:
                source.title = result.title[:512]

        new = await upsert_items(db, source, result.items)
        source.last_error = None
        log.info("polled %s: %d items, %d new", source.label, len(result.items), new)
    except Exception as e:  # one broken source must not stop the others
        await db.rollback()
        source = await db.get(Source, source_id)
        source.last_error = f"{type(e).__name__}: {e}"[:2000]
        new = 0
        log.warning("poll of %s failed: %s", label, e, exc_info=not isinstance(e, httpx.HTTPError))
    source.last_polled_at = now
    await db.commit()
    return new


async def _attach_comments(reddit: RedditFetcher, item: FetchedItem, top_n: int) -> None:
    try:
        item.comments_text = await reddit.fetch_comments(item.extra["id"], top_n)
        item.comments_fetched = True
    except httpx.HTTPError as e:
        log.warning("comments for %s failed: %s", item.external_id, e)


async def _known_ids(db: AsyncSession, external_ids: list[str] | None, source_id: int | None = None) -> set[str]:
    q = select(Item.external_id)
    if external_ids is not None:
        q = q.where(Item.external_id.in_(external_ids))
    if source_id is not None:
        q = q.join(ItemSource, ItemSource.item_id == Item.id).where(ItemSource.source_id == source_id)
    return set((await db.scalars(q)).all())


async def upsert_items(db: AsyncSession, source: Source, fetched: list[FetchedItem]) -> int:
    """Insert new items, refresh known ones, and link every item to this source.

    Items are deduplicated by external id and by canonical URL, so the same article
    posted to a subreddit and published in an RSS feed becomes one item with two sources.
    """
    if not fetched:
        return 0
    now = datetime.now(UTC)
    by_ext = {
        i.external_id: i
        for i in (await db.scalars(select(Item).where(Item.external_id.in_([f.external_id for f in fetched])))).all()
    }
    canon = {f.external_id: canonicalize_url(f.url) for f in fetched}
    by_canon = {
        i.canonical_url: i
        for i in (
            await db.scalars(
                select(Item).where(
                    Item.canonical_url.in_(set(canon.values())),
                    Item.published_at > now - timedelta(days=30),
                )
            )
        ).all()
    }

    new_count = 0
    linked: list[int] = []
    for f in fetched:
        item = by_ext.get(f.external_id) or by_canon.get(canon[f.external_id])
        if item is None:
            item = Item(
                external_id=f.external_id,
                url=f.url,
                canonical_url=canon[f.external_id],
                discussion_url=f.discussion_url,
                title=f.title[:2000],
                author=f.author,
                body=f.body,
                comments_text=f.comments_text,
                comments_fetched_at=now if f.comments_fetched else None,
                comments_fetch_count=1 if f.comments_fetched else 0,
                score=f.score,
                num_comments=f.num_comments,
                published_at=f.published_at,
            )
            db.add(item)
            await db.flush()
            by_ext[f.external_id] = item
            by_canon[item.canonical_url] = item
            new_count += 1
        elif item.external_id == f.external_id:
            if f.score is not None:
                item.score = f.score
            if f.num_comments is not None:
                item.num_comments = f.num_comments
            if f.comments_fetched:
                item.comments_text = f.comments_text
        if item.id not in linked:
            linked.append(item.id)

    await db.execute(
        pg_insert(ItemSource)
        .values([{"item_id": i, "source_id": source.id} for i in linked])
        .on_conflict_do_nothing()
    )
    await db.flush()
    return new_count


async def refresh_comments(sessionmaker: async_sessionmaker, fetchers: Fetchers, settings: Settings) -> int:
    """Re-read comments of relevant Reddit posts as they age (fixes often come later)."""
    if not fetchers.reddit.oauth:
        return 0
    now = datetime.now(UTC)
    conditions = [
        and_(Item.comments_fetch_count <= n, Item.published_at < now - age)
        for n, age in enumerate(COMMENT_REFRESH_AGES, start=1)
    ]
    async with sessionmaker() as db:
        top_n = await db.scalar(select(func.max(UserSettings.comments_top_n))) or 10
        items = (
            await db.scalars(
                select(Item)
                .where(Item.external_id.like("reddit:%"))
                .where(Item.published_at > now - timedelta(days=2))
                .where(Item.comments_fetch_count < len(COMMENT_REFRESH_AGES) + 1)
                .where(or_(*conditions))
                .where(
                    exists().where(
                        Evaluation.item_id == Item.id, Evaluation.relevance >= COMMENT_REFRESH_MIN_RELEVANCE
                    )
                )
                .limit(COMMENT_REFRESH_BATCH)
            )
        ).all()
        for item in items:
            post_id = item.external_id.removeprefix("reddit:t3_")
            try:
                item.comments_text = await fetchers.reddit.fetch_comments(post_id, top_n)
            except httpx.HTTPError as e:
                log.warning("comment refresh for %s failed: %s", item.external_id, e)
            # Advance to the next refresh stage whether or not the fetch succeeded
            elapsed = now - item.published_at
            item.comments_fetch_count = 1 + sum(1 for age in COMMENT_REFRESH_AGES if elapsed >= age)
            item.comments_fetched_at = now
        await db.commit()
        return len(items)
