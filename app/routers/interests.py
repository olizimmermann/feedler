from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user
from app.db import get_db
from app.models import Interest, Subscription, User
from app.templating import render

router = APIRouter(prefix="/settings/interests")


async def _source_id(db: AsyncSession, user: User, raw: str) -> int | None:
    if not raw.isdigit():
        return None
    return int(raw) if await db.get(Subscription, (user.id, int(raw))) else None


@router.get("")
async def interests_page(request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    interests = (
        await db.scalars(select(Interest).where(Interest.user_id == user.id).order_by(Interest.created_at))
    ).all()
    subs = (await db.scalars(select(Subscription).where(Subscription.user_id == user.id))).all()
    return render(request, "settings/interests.html", interests=interests, sources=[s.source for s in subs])


@router.post("")
async def create_interest(name: str = Form(...), description: str = Form(""), source_id: str = Form(""),
                          include_comments: bool = Form(False), user: User = Depends(current_user),
                          db: AsyncSession = Depends(get_db)):
    if name.strip():
        db.add(Interest(user_id=user.id, name=name.strip()[:200], description=description.strip(),
                        source_id=await _source_id(db, user, source_id), include_comments=include_comments))
        await db.commit()
    return RedirectResponse("/settings/interests", status_code=303)


async def _own(db: AsyncSession, user: User, interest_id: int) -> Interest:
    interest = await db.get(Interest, interest_id)
    if not interest or interest.user_id != user.id:
        raise HTTPException(404)
    return interest


@router.post("/{interest_id}")
async def update_interest(interest_id: int, name: str = Form(...), description: str = Form(""),
                          source_id: str = Form(""), include_comments: bool = Form(False),
                          enabled: bool = Form(False), user: User = Depends(current_user),
                          db: AsyncSession = Depends(get_db)):
    interest = await _own(db, user, interest_id)
    interest.name = name.strip()[:200] or interest.name
    interest.description = description.strip()
    interest.source_id = await _source_id(db, user, source_id)
    interest.include_comments = include_comments
    interest.enabled = enabled
    await db.commit()
    return RedirectResponse("/settings/interests", status_code=303)


@router.post("/{interest_id}/delete")
async def delete_interest(interest_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.delete(await _own(db, user, interest_id))
    await db.commit()
    return RedirectResponse("/settings/interests", status_code=303)
