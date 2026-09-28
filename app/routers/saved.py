import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.fetchers.article import extract_title_and_text
from app.models import SavedItem, User
from app.templating import render

log = logging.getLogger(__name__)
router = APIRouter()


def parse_tags(raw: str) -> list[str]:
    tags = []
    for t in raw.replace(",", " ").split():
        t = t.strip().lstrip("#").lower()[:64]
        if t and t not in tags:
            tags.append(t)
    return tags


async def archive_saved_item(saved_id: int, fallback_text: str | None = None) -> None:
    """Fetch the page behind a saved link and keep its text, so it survives link rot."""
    async with SessionLocal() as db:
        saved = await db.get(SavedItem, saved_id)
        if not saved:
            return
        async with httpx.AsyncClient(headers={"User-Agent": get_settings().reddit_user_agent}) as client:
            title, text = await extract_title_and_text(client, saved.url)
        saved.archived_text = text or fallback_text or saved.archived_text
        if title and (not saved.title or saved.title == saved.url):
            saved.title = title[:2000]
        await db.commit()


@router.get("/saved")
async def saved_page(request: Request, q: str = "", tag: str = "", user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)):
    query = select(SavedItem).where(SavedItem.user_id == user.id)
    if tag:
        query = query.where(SavedItem.tags.any(tag))
    if q.strip():
        tsq = func.websearch_to_tsquery("english", q.strip())
        query = query.where(SavedItem.search.op("@@")(tsq)).order_by(func.ts_rank(SavedItem.search, tsq).desc())
    query = query.order_by(SavedItem.created_at.desc()).limit(500)
    items = (await db.scalars(query)).all()
    all_tags = (
        await db.execute(
            select(func.unnest(SavedItem.tags).label("t"), func.count())
            .where(SavedItem.user_id == user.id)
            .group_by("t")
            .order_by(func.count().desc())
        )
    ).all()
    template = "partials/saved_list.html" if request.headers.get("hx-target") == "saved-list" else "saved.html"
    return render(request, template, items=items, q=q, tag=tag, all_tags=all_tags)


@router.post("/saved")
async def add_url(background: BackgroundTasks, url: str = Form(...), note: str = Form(""), tags: str = Form(""),
                  user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    saved = await db.scalar(select(SavedItem).where(SavedItem.user_id == user.id, SavedItem.url == url))
    if not saved:
        saved = SavedItem(user_id=user.id, url=url, title=url, note=note.strip(), tags=parse_tags(tags))
        db.add(saved)
        await db.commit()
        background.add_task(archive_saved_item, saved.id)
    return RedirectResponse("/saved", status_code=303)


async def _own(db: AsyncSession, user: User, saved_id: int) -> SavedItem:
    saved = await db.get(SavedItem, saved_id)
    if not saved or saved.user_id != user.id:
        raise HTTPException(404)
    return saved


@router.get("/saved/{saved_id}")
async def saved_detail(request: Request, saved_id: int, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    return render(request, "saved_item.html", s=await _own(db, user, saved_id))


@router.post("/saved/{saved_id}/edit")
async def edit_saved(request: Request, saved_id: int, note: str = Form(""), tags: str = Form(""),
                     user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    saved = await _own(db, user, saved_id)
    saved.note = note.strip()
    saved.tags = parse_tags(tags)
    await db.commit()
    return render(request, "partials/saved_row.html", s=saved)


@router.post("/saved/{saved_id}/delete")
async def delete_saved(saved_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.delete(await _own(db, user, saved_id))
    await db.commit()
    return Response(content="", media_type="text/html")
