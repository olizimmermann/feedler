from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import admin_user, registration_open
from app.db import get_db
from app.models import AppSetting, SavedItem, Subscription, User, Vote
from app.templating import render

router = APIRouter(prefix="/admin")


@router.get("")
async def admin_page(request: Request, user: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    def count(model):
        return select(func.count()).where(model.user_id == User.id).scalar_subquery()

    users = (
        await db.execute(
            select(User, count(Subscription), count(Vote), count(SavedItem)).order_by(User.created_at)
        )
    ).all()
    return render(request, "admin.html", users=users, reg_open=await registration_open(db))


@router.post("/registration")
async def toggle_registration(user: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    value = "false" if await registration_open(db) else "true"
    await db.execute(
        pg_insert(AppSetting)
        .values(key="registration_open", value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value})
    )
    await db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/admin")
async def toggle_admin(user_id: int, user: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    target = await db.get(User, user_id)
    if not target or target.id == user.id:
        raise HTTPException(400, "You can't change your own admin flag.")
    target.is_admin = not target.is_admin
    await db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(user_id: int, user: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    target = await db.get(User, user_id)
    if not target or target.id == user.id:
        raise HTTPException(400, "You can't delete yourself.")
    await db.delete(target)
    await db.commit()
    return RedirectResponse("/admin", status_code=303)
