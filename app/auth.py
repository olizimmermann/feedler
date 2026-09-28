import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import AppSetting, User

_hasher = PasswordHasher()
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(32)
    return token


async def csrf_protect(request: Request) -> None:
    """Require the session's CSRF token on unsafe requests (form field or HTMX header)."""
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get("csrf")
    sent = request.headers.get("x-csrf-token")
    if not sent:
        form = await request.form()
        sent = form.get("csrf_token")
    if not expected or not sent or not hmac.compare_digest(str(sent), expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token. Reload the page and try again.")


class LoginRequired(Exception):
    pass


async def optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    user = await db.get(User, user_id) if user_id else None
    request.state.user = user
    return user


async def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise LoginRequired()
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    return user


async def registration_open(db: AsyncSession) -> bool:
    has_users = await db.scalar(select(User.id).limit(1))
    if not has_users:
        return True
    value = await db.scalar(select(AppSetting.value).where(AppSetting.key == "registration_open"))
    return value != "false"
