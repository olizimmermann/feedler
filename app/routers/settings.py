from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user
from app.config import PROVIDERS, get_settings
from app.db import get_db
from app.llm.backoff import user_llm_status
from app.models import LLMCall, PreferenceProfile, User
from app.pipeline.classify import latest_profile
from app.pipeline.profile import ProfileError, refine_profile
from app.templating import render

router = APIRouter(prefix="/settings")


async def _settings_context(db: AsyncSession, user: User) -> dict:
    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=30)
    usage = (
        await db.execute(
            select(
                LLMCall.provider,
                LLMCall.model,
                LLMCall.purpose,
                func.count(),
                func.sum(LLMCall.tokens_in),
                func.sum(LLMCall.tokens_out),
                func.count().filter(LLMCall.ok.is_(False)),
            )
            .where(LLMCall.user_id == user.id, LLMCall.created_at >= since)
            .group_by(LLMCall.provider, LLMCall.model, LLMCall.purpose)
            .order_by(LLMCall.provider, LLMCall.model)
        )
    ).all()
    last_error = await db.scalar(
        select(LLMCall)
        .where(LLMCall.user_id == user.id, LLMCall.created_at >= datetime.now(UTC) - timedelta(days=1))
        .order_by(LLMCall.created_at.desc())
        .limit(1)
    )
    history = (
        await db.scalars(
            select(PreferenceProfile)
            .where(PreferenceProfile.user_id == user.id)
            .order_by(PreferenceProfile.version.desc())
            .limit(20)
        )
    ).all()
    llm_status = await user_llm_status(db, user.settings, settings)
    return dict(
        llm=llm_status,
        providers=[(p, settings.provider_configured(p), settings.default_model(p)) for p in PROVIDERS],
        usage=usage,
        last_call=last_error,
        profile=history[0] if history else None,
        history=history,
    )


@router.get("")
async def settings_page(request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return render(request, "settings/general.html", **await _settings_context(db, user),
                  message=request.query_params.get("msg"), error=request.query_params.get("err"))


@router.post("/llm")
async def update_llm(provider: str = Form(...), model: str = Form(""), fallbacks: list[str] = Form([]),
                     threshold: int = Form(60),
                     comments_top_n: int = Form(10), user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)):
    if provider not in PROVIDERS:
        raise HTTPException(400)
    us = user.settings
    us.llm_provider = provider
    us.llm_model = model.strip() or None
    us.llm_fallbacks = [f for f in dict.fromkeys(fallbacks) if f in PROVIDERS and f != provider]
    us.relevance_threshold = max(0, min(100, threshold))
    us.comments_top_n = max(0, min(50, comments_top_n))
    await db.commit()
    return RedirectResponse("/settings?msg=Saved", status_code=303)


@router.post("/profile")
async def save_profile(text: str = Form(...), user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    current = await latest_profile(db, user.id)
    if text.strip() and (not current or text.strip() != current.text.strip()):
        db.add(PreferenceProfile(user_id=user.id, version=(current.version + 1) if current else 1,
                                 text=text.strip(), origin="manual", locked=current.locked if current else False))
        await db.commit()
    return RedirectResponse("/settings?msg=Profile+saved#profile", status_code=303)


@router.post("/profile/lock")
async def toggle_lock(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    current = await latest_profile(db, user.id)
    if current:
        current.locked = not current.locked
        await db.commit()
    return RedirectResponse("/settings#profile", status_code=303)


@router.post("/profile/refine")
async def refine_now(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    try:
        profile = await refine_profile(db, user, get_settings())
    except ProfileError as e:
        return RedirectResponse(f"/settings?err={quote_plus(str(e))}#profile", status_code=303)
    except IntegrityError:
        return RedirectResponse("/settings?err=A+refinement+was+already+running#profile", status_code=303)
    return RedirectResponse(f"/settings?msg=Profile+refined+to+v{profile.version}#profile", status_code=303)


@router.post("/profile/restore/{version}")
async def restore(version: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    old = await db.scalar(
        select(PreferenceProfile).where(PreferenceProfile.user_id == user.id, PreferenceProfile.version == version)
    )
    current = await latest_profile(db, user.id)
    if old and current and old.id != current.id:
        db.add(PreferenceProfile(user_id=user.id, version=current.version + 1, text=old.text, origin="manual",
                                 locked=current.locked))
        await db.commit()
    return RedirectResponse(f"/settings?msg=Restored+v{version}#profile", status_code=303)
