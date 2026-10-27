from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import LoginRequired, csrf_protect
from app.config import get_settings
from app.routers import admin, auth, feed, interests, saved, settings, sources

settings_ = get_settings()
if settings_.secret_key in ("", "change-me"):
    raise RuntimeError(
        "SECRET_KEY is not set. Generate one with: "
        "python -c \"import secrets; print(secrets.token_urlsafe(48))\" and put it in .env"
    )

app = FastAPI(title="Feedler", docs_url=None, redoc_url=None, dependencies=[Depends(csrf_protect)])
app.add_middleware(
    SessionMiddleware,
    secret_key=settings_.secret_key,
    session_cookie="feedler_session",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=settings_.base_url.startswith("https://"),
)


# Inline scripts, event handler attributes and inline styles are all blocked: keep JS in app.js
CSP = (
    "default-src 'self'; img-src 'self' https: data:; script-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

for module in (auth, feed, saved, sources, interests, settings, admin):
    app.include_router(module.router)


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired) -> Response:
    if request.headers.get("hx-request"):
        return Response(status_code=204, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True}
