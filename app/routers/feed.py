from dataclasses import dataclass
from datetime import UTC, datetime
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user
from app.config import get_settings
from app.db import get_db
from app.llm.backoff import user_llm_status
from app.pipeline.classify import queue_filter
from app.models import Evaluation, Interest, Item, ItemSource, SavedItem, Subscription, User, UserSettings, Vote
from app.routers.saved import archive_saved_item
from app.templating import render

router = APIRouter()
PAGE_SIZE = 30


@dataclass
class FeedFilters:
    view: str = "feed"  # feed | filtered | all
    interest: int | None = None
    source: int | None = None
    q: str = ""
    sort: str = "new"  # new | top
    unread: bool = False

    @classmethod
    def from_request(cls, request: Request) -> "FeedFilters":
        p = request.query_params
        return cls(
            view=p.get("view", "feed") if p.get("view") in ("feed", "filtered", "all") else "feed",
            interest=int(p["interest"]) if p.get("interest", "").isdigit() else None,
            source=int(p["source"]) if p.get("source", "").isdigit() else None,
            q=p.get("q", "").strip(),
            sort="top" if p.get("sort") == "top" else "new",
            unread=p.get("unread") == "1",
        )


@dataclass
class Card:
    item: Item
    ev: Evaluation
    vote: int
    saved: bool
    interests: list[str]


async def load_cards(db: AsyncSession, user: User, rows) -> list[Card]:
    names = dict((await db.execute(select(Interest.id, Interest.name).where(Interest.user_id == user.id))).all())
    return [
        Card(item, ev, vote or 0, bool(saved), [names[i] for i in ev.matched_interest_ids if i in names])
        for item, ev, vote, saved in rows
    ]


def card_query(user: User):
    return (
        select(
            Item,
            Evaluation,
            Vote.value,
            exists().where(SavedItem.user_id == user.id, SavedItem.item_id == Item.id),
        )
        .join(Evaluation, and_(Evaluation.item_id == Item.id, Evaluation.user_id == user.id))
        .outerjoin(Vote, and_(Vote.item_id == Item.id, Vote.user_id == user.id))
    )


async def query_feed(db: AsyncSession, user: User, f: FeedFilters, offset: int) -> list[Card]:
    threshold = user.settings.relevance_threshold
    q = card_query(user).where(Evaluation.hidden.is_(False))
    if f.view == "feed":
        q = q.where(Evaluation.relevance >= threshold)
    elif f.view == "filtered":
        q = q.where(Evaluation.relevance < threshold)
    if f.interest:
        q = q.where(Evaluation.matched_interest_ids.any(f.interest))
    if f.source:
        q = q.where(exists().where(ItemSource.item_id == Item.id, ItemSource.source_id == f.source))
    if f.unread:
        q = q.where(Evaluation.read_at.is_(None))
    if f.q:
        q = q.where(Item.search.op("@@")(func.websearch_to_tsquery("english", f.q)))
    order = [Evaluation.relevance.desc(), Item.published_at.desc()] if f.sort == "top" else [Item.published_at.desc()]
    rows = (await db.execute(q.order_by(*order).offset(offset).limit(PAGE_SIZE))).all()
    return await load_cards(db, user, rows)


async def get_card(db: AsyncSession, user: User, item_id: int) -> Card:
    row = (await db.execute(card_query(user).where(Item.id == item_id))).first()
    if not row:
        raise HTTPException(404)
    return (await load_cards(db, user, [row]))[0]


@router.get("/")
async def feed_page(request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    f = FeedFilters.from_request(request)
    cards = await query_feed(db, user, f, 0)
    subs = (await db.scalars(select(Subscription).where(Subscription.user_id == user.id))).all()
    interests = (await db.scalars(select(Interest).where(Interest.user_id == user.id).order_by(Interest.name))).all()
    pending = await db.scalar(select(func.count(Item.id)).where(*queue_filter(user.id, get_settings())))
    us = user.settings
    llm_paused = await user_llm_status(db, us.llm_provider, us.llm_model, get_settings())
    return render(
        request, "feed.html", cards=cards, llm_paused=llm_paused, f=f, next_offset=PAGE_SIZE if len(cards) == PAGE_SIZE else None,
        sources=[s.source for s in subs], interests=interests, pending=pending,
    )


@router.get("/feed/page")
async def feed_more(request: Request, offset: int = 0, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)):
    f = FeedFilters.from_request(request)
    cards = await query_feed(db, user, f, offset)
    return render(request, "partials/card_list.html", cards=cards, f=f,
                  next_offset=offset + PAGE_SIZE if len(cards) == PAGE_SIZE else None)


@router.get("/items/{item_id}")
async def item_detail(request: Request, item_id: int, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)):
    card = await get_card(db, user, item_id)
    if card.ev.read_at is None:
        card.ev.read_at = datetime.now(UTC)
        await db.commit()
    return render(request, "item.html", card=card)


@router.post("/items/{item_id}/vote")
async def vote(request: Request, item_id: int, value: int = Form(...), note: str = Form(""),
               user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if value not in (-1, 0, 1):
        raise HTTPException(400)
    existing = await db.get(Vote, (user.id, item_id))
    us: UserSettings = user.settings
    if value == 0 or (existing and existing.value == value and not note):
        # Clicking the active vote again removes it
        if existing:
            await db.delete(existing)
    elif existing:
        existing.value = value
        existing.note = note.strip() or existing.note
        existing.created_at = datetime.now(UTC)
        us.votes_since_refine += 1
    else:
        if not await db.get(Item, item_id):
            raise HTTPException(404)
        db.add(Vote(user_id=user.id, item_id=item_id, value=value, note=note.strip() or None))
        us.votes_since_refine += 1
    await db.commit()
    return render(request, "partials/card.html", card=await get_card(db, user, item_id))


@router.post("/items/{item_id}/read")
async def mark_read(request: Request, item_id: int, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)):
    ev = await db.scalar(select(Evaluation).where(Evaluation.item_id == item_id, Evaluation.user_id == user.id))
    if ev and ev.read_at is None:
        ev.read_at = datetime.now(UTC)
        await db.commit()
    return Response(status_code=204)


@router.post("/items/{item_id}/hide")
async def hide(item_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    ev = await db.scalar(select(Evaluation).where(Evaluation.item_id == item_id, Evaluation.user_id == user.id))
    if ev:
        ev.hidden = True
        await db.commit()
    return Response(content="", media_type="text/html")


@router.post("/items/{item_id}/save")
async def toggle_save(request: Request, item_id: int, background: BackgroundTasks,
                      user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    item = await db.get(Item, item_id)
    if not item:
        raise HTTPException(404)
    saved = await db.scalar(select(SavedItem).where(SavedItem.user_id == user.id, SavedItem.item_id == item_id))
    if saved:
        await db.delete(saved)
    else:
        saved = await db.scalar(select(SavedItem).where(SavedItem.user_id == user.id, SavedItem.url == item.url))
        if saved:
            saved.item_id = item.id
        else:
            is_self_post = item.discussion_url and item.url == item.discussion_url
            text = "\n\n".join(t for t in (item.body, item.comments_text) if t)
            saved = SavedItem(user_id=user.id, item_id=item.id, url=item.url, title=item.title,
                              archived_text=text if is_self_post else None)
            db.add(saved)
            await db.flush()
            if not is_self_post:
                background.add_task(archive_saved_item, saved.id, fallback_text=item.body)
    await db.commit()
    return render(request, "partials/card.html", card=await get_card(db, user, item_id))


@router.get("/u/{token}/feed.xml")
async def output_feed(token: str, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.feed_token == token))
    if not user:
        raise HTTPException(404)
    rows = (
        await db.execute(
            card_query(user)
            .where(Evaluation.relevance >= user.settings.relevance_threshold, Evaluation.hidden.is_(False))
            .order_by(Item.published_at.desc())
            .limit(100)
        )
    ).all()
    base = get_settings().base_url.rstrip("/")
    entries = []
    for item, ev, _vote, _saved in rows:
        content = f"<p><b>{ev.relevance}/100</b> {escape(ev.reason)}</p><p>{escape(ev.summary)}</p>"
        if item.discussion_url and item.discussion_url != item.url:
            content += f"<p><a href={quoteattr(item.discussion_url)}>Discussion</a></p>"
        entries.append(
            "<entry>"
            f"<id>{escape(base)}/items/{item.id}</id>"
            f"<title>{escape(item.title)}</title>"
            f"<link href={quoteattr(item.url)}/>"
            f"<updated>{item.published_at.isoformat()}</updated>"
            f'<content type="html">{escape(content)}</content>'
            "</entry>"
        )
    updated = rows[0][0].published_at.isoformat() if rows else datetime.now(UTC).isoformat()
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Feedler: filtered feed</title>"
        f"<id>{escape(base)}/u/{token}</id>"
        f"<updated>{updated}</updated>" + "".join(entries) + "</feed>"
    )
    return Response(content=xml, media_type="application/atom+xml")
