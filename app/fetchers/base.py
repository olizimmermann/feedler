import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

log = logging.getLogger(__name__)

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid", "si"}


@dataclass
class FetchedItem:
    external_id: str
    url: str
    title: str
    published_at: datetime
    body: str = ""
    author: str | None = None
    discussion_url: str | None = None
    score: int | None = None
    num_comments: int | None = None
    comments_text: str = ""
    comments_fetched: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    items: list[FetchedItem]
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    title: str | None = None


def canonicalize_url(url: str) -> str:
    """Normalize a URL so the same article from different sources dedupes."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = parts.netloc.lower()
    for prefix in ("www.", "old.", "new.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    if host == "redd.it":
        host = "reddit.com"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


async def get_with_backoff(
    client: httpx.AsyncClient, url: str, *, retries: int = 4, **kwargs
) -> httpx.Response:
    """GET with exponential backoff on 429 / 5xx, honouring Retry-After."""
    delay = 2.0
    for attempt in range(retries + 1):
        resp = await client.get(url, **kwargs)
        if resp.status_code != 429 and resp.status_code < 500:
            return resp
        if attempt == retries:
            return resp
        retry_after = resp.headers.get("retry-after")
        wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
        log.warning("GET %s -> %s, retrying in %.0fs", url, resp.status_code, wait)
        await asyncio.sleep(min(wait, 120))
        delay *= 2
    raise AssertionError("unreachable")
