from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, optional_user, registration_open, verify_password
from app.config import get_settings
from app.db import get_db
from app.models import User, UserSettings
from app.templating import render

router = APIRouter()


@router.get("/login")
async def login_page(request: Request, user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_db)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", can_register=await registration_open(db))


@router.post("/login")
async def login(
    request: Request, email: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_db)
):
    user = await db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if not user or not verify_password(user.password_hash, password):
        return render(request, "login.html", error="Wrong email or password.", email=email,
                      can_register=await registration_open(db))
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.get("/register")
async def register_page(request: Request, db: AsyncSession = Depends(get_db)):
    if not await registration_open(db):
        return render(request, "login.html", error="Registration is closed.", can_register=False)
    return render(request, "register.html")


@router.post("/register")
async def register(
    request: Request, email: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_db)
):
    if not await registration_open(db):
        return render(request, "login.html", error="Registration is closed.", can_register=False)
    email = email.strip().lower()
    if "@" not in email or len(password) < 8:
        return render(request, "register.html", error="Use a valid email and a password of at least 8 characters.",
                      email=email)
    if await db.scalar(select(User.id).where(func.lower(User.email) == email)):
        return render(request, "register.html", error="That email is already registered.", email=email)

    is_first = not await db.scalar(select(User.id).limit(1))
    user = User(email=email, password_hash=hash_password(password), is_admin=is_first)
    db.add(user)
    await db.flush()
    settings = get_settings()
    db.add(UserSettings(user_id=user.id, llm_provider=settings.default_llm_provider,
                        llm_fallbacks=settings.fallback_providers))
    await db.commit()
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/settings/sources?welcome=1", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
