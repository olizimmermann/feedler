import re
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user
from app.config import get_settings
from app.db import get_db
from app.models import Source, Subscription, User
from app.templating import render

router = APIRouter(prefix="/settings/sources")

_SUB_RE = re.compile(r"^(?:https?://(?:www\.|old\.)?reddit\.com)?/?(?:r/)?([A-Za-z0-9_]{2,21})/?$")


def parse_source(raw: str) -> tuple[str, str] | None:
    """Turn user input into (kind, identifier). Accepts r/name, reddit URLs, or feed URLs."""
    raw = raw.strip()
    m = _SUB_RE.match(raw)
    if m and ("reddit.com" in raw or raw.startswith(("r/", "/r/")) or "/" not in raw and "." not in raw):
        return "reddit", m.group(1).lower()
    if raw.startswith(("http://", "https://")):
        return "rss", raw
    if "." in raw and " " not in raw:
        return "rss", "https://" + raw
    return None


async def subscribe(db: AsyncSession, user: User, kind: str, identifier: str, title: str | None = None) -> bool:
    source = await db.scalar(select(Source).where(Source.kind == kind, Source.identifier == identifier))
    if not source:
        source = Source(
            kind=kind,
            identifier=identifier,
            title=(title or identifier)[:512],
            poll_interval_minutes=get_settings().default_poll_interval_minutes,
        )
        db.add(source)
        await db.flush()
    if await db.get(Subscription, (user.id, source.id)):
        return False
    db.add(Subscription(user_id=user.id, source_id=source.id))
    return True


@router.get("")
async def sources_page(request: Request, welcome: int = 0, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    subs = (await db.scalars(select(Subscription).where(Subscription.user_id == user.id))).all()
    sources = sorted((s.source for s in subs), key=lambda s: (s.kind, s.label.lower()))
    return render(request, "settings/sources.html", sources=sources, welcome=welcome,
                  message=request.query_params.get("msg"), error=request.query_params.get("err"))


@router.post("")
async def add_source(value: str = Form(...), user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    parsed = parse_source(value)
    if not parsed:
        return RedirectResponse("/settings/sources?err=Enter+r/subreddit+or+a+feed+URL", status_code=303)
    await subscribe(db, user, *parsed)
    await db.commit()
    return RedirectResponse("/settings/sources", status_code=303)


@router.post("/{source_id}/unsubscribe")
async def unsubscribe(source_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    sub = await db.get(Subscription, (user.id, source_id))
    if sub:
        await db.delete(sub)
        await db.commit()
    return RedirectResponse("/settings/sources", status_code=303)


@router.post("/opml")
async def import_opml(file: UploadFile = File(...), user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)):
    try:
        root = ET.fromstring(await file.read())
    except ET.ParseError:
        return RedirectResponse("/settings/sources?err=Could+not+parse+the+OPML+file", status_code=303)
    added = 0
    for outline in root.iter("outline"):
        url = outline.get("xmlUrl")
        if url:
            added += await subscribe(db, user, "rss", url.strip(), outline.get("title") or outline.get("text"))
    await db.commit()
    return RedirectResponse(f"/settings/sources?msg=Imported+{added}+feeds", status_code=303)
