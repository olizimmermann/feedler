"""Implicit feedback: turn how a user reads posts into signals for the preference profile."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Engagement, Item, UserSettings

EVENTS = ("original", "link", "comments")
MAX_PING_SECONDS = 60  # the reader reports every 15s, so anything above this is not a real ping
MAX_READ_SECONDS = 3600
WORDS_PER_SECOND = 4  # about 240 words per minute
SETTLE_AFTER = timedelta(minutes=10)  # a short visit only counts as a skip once the reader is surely closed
RETENTION = timedelta(days=90)


async def get_engagement(db: AsyncSession, user_id: int, item_id: int) -> Engagement:
    """The row for this user and item, created if missing and locked for the update."""
    await db.execute(insert(Engagement).values(user_id=user_id, item_id=item_id).on_conflict_do_nothing())
    return await db.get(Engagement, (user_id, item_id), with_for_update=True, populate_existing=True)


async def record_open(db: AsyncSession, user_id: int, item_id: int) -> None:
    eng = await get_engagement(db, user_id, item_id)
    eng.opens += 1
    eng.last_seen_at = datetime.now(UTC)


async def record_activity(db: AsyncSession, us: UserSettings, item: Item, seconds: int = 0, scroll: int = 0,
                          event: str = "") -> Engagement:
    """Add a reader ping. Time is capped by the wall-clock time since the last ping, so it can't be inflated."""
    now = datetime.now(UTC)
    eng = await get_engagement(db, us.user_id, item.id)
    elapsed = (now - eng.last_seen_at).total_seconds() if eng.last_seen_at else MAX_PING_SECONDS
    seconds = int(max(0, min(seconds, MAX_PING_SECONDS, elapsed + 5)))
    eng.read_seconds = min(MAX_READ_SECONDS, eng.read_seconds + seconds)
    eng.max_scroll = max(eng.max_scroll, max(0, min(100, scroll)))
    if event == "original":
        eng.original_opens += 1
    elif event == "link":
        eng.link_clicks += 1
    elif event == "comments":
        eng.comments_seen = True
    eng.last_seen_at = now
    # Count each clearly read post once towards the next profile refinement
    if not eng.counted and reading_signal(eng, item) in ("followed", "read"):
        eng.counted = True
        us.signals_since_refine += 1
    return eng


def expected_seconds(item: Item) -> int:
    words = len((item.article_text or item.body or "").split()) + len(item.title.split())
    return max(15, words // WORDS_PER_SECOND)


def reading_signal(eng: Engagement, item: Item, now: datetime | None = None) -> str | None:
    """followed (opened links), read (spent real time), skipped (opened and left at once), or None (unclear)."""
    if eng.original_opens or eng.link_clicks:
        return "followed"
    expected = expected_seconds(item)
    if eng.read_seconds >= 20 and (eng.read_seconds >= min(expected // 2, 120) or eng.comments_seen):
        return "read"
    settled = (now or datetime.now(UTC)) - eng.last_seen_at >= SETTLE_AFTER
    if settled and eng.opens and eng.read_seconds < 8 and eng.max_scroll < 30 and not eng.comments_seen:
        return "skipped"
    return None


async def prune_engagements(sessionmaker: async_sessionmaker) -> int:
    """Reading data is only useful while recent, so don't keep it forever."""
    async with sessionmaker() as db:
        result = await db.execute(delete(Engagement).where(Engagement.last_seen_at < datetime.now(UTC) - RETENTION))
        await db.commit()
        return result.rowcount
