import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.auth import csrf_token
from app.config import get_settings

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

_STATIC = Path(__file__).parent / "static"
# Changes whenever the CSS/JS change, so browsers and home-screen apps never run stale assets
ASSET_VERSION = hashlib.sha256(
    b"".join((_STATIC / name).read_bytes() for name in ("app.css", "app.js"))
).hexdigest()[:10]
templates.env.globals["asset_v"] = ASSET_VERSION


def timeago(value: datetime | None) -> str:
    if value is None:
        return ""
    seconds = int((datetime.now(UTC) - value).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return "now"


def ago(value: datetime | None) -> str:
    short = timeago(value)
    return "just now" if short == "now" else f"{short} ago"


def until(value: datetime | None) -> str:
    if value is None:
        return ""
    seconds = int((value - datetime.now(UTC)).total_seconds())
    if seconds < 60:
        return "in under a minute"
    return f"in {seconds // 60}m" if seconds < 3600 else f"in {seconds // 3600}h {seconds % 3600 // 60}m"


def score_class(relevance: int | None) -> str:
    if relevance is None:
        return "score-none"
    if relevance >= 80:
        return "score-high"
    if relevance >= 60:
        return "score-mid"
    return "score-low"


templates.env.filters["timeago"] = timeago
templates.env.filters["ago"] = ago
templates.env.filters["until"] = until
templates.env.filters["score_class"] = score_class


def render(request: Request, name: str, **context):
    context.setdefault("user", getattr(request.state, "user", None))
    context["csrf"] = csrf_token(request)
    context["settings"] = get_settings()
    return templates.TemplateResponse(request, name, context)
