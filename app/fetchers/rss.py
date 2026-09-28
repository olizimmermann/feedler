import calendar
import hashlib
import html
import logging
import re
from datetime import UTC, datetime

import feedparser
import httpx

from app.fetchers.article import extract_article
from app.fetchers.base import FetchedItem, FetchResult, get_with_backoff

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_END_RE = re.compile(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|blockquote|pre|tr)>")
SHORT_BODY_CHARS = 400
MAX_EXTRACTIONS_PER_POLL = 10


def html_to_text(value: str, paragraphs: bool = False) -> str:
    """Strip tags. With paragraphs=True, block elements become blank-line separated paragraphs."""
    if not paragraphs:
        return " ".join(html.unescape(_TAG_RE.sub(" ", value or "")).split())
    text = html.unescape(_TAG_RE.sub(" ", _BLOCK_END_RE.sub("\n\n", value or "")))
    blocks = (" ".join(block.split()) for block in re.split(r"\n\s*\n", text))
    return "\n\n".join(b for b in blocks if b)


class RSSFetcher:
    def __init__(self, user_agent: str, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            timeout=30, headers={"User-Agent": user_agent}, follow_redirects=True
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        known_ids: set[str] | None = None,
    ) -> FetchResult:
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        resp = await get_with_backoff(self.client, url, headers=headers)
        if resp.status_code == 304:
            return FetchResult(items=[], etag=etag, last_modified=last_modified, not_modified=True)
        resp.raise_for_status()

        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            raise ValueError(f"Not a valid feed: {parsed.bozo_exception}")

        items = [parse_entry(e, url) for e in parsed.entries]
        items = [i for i in items if i is not None]

        # Feeds that only ship a teaser: pull the full article text for new entries
        known_ids = known_ids or set()
        extracted = 0
        for item in items:
            if extracted >= MAX_EXTRACTIONS_PER_POLL:
                break
            if item.external_id in known_ids or len(item.body) >= SHORT_BODY_CHARS:
                continue
            text = await extract_article(self.client, item.url)
            extracted += 1
            if text and len(text) > len(item.body):
                item.body = text

        return FetchResult(
            items=items,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            title=parsed.feed.get("title"),
        )


def parse_entry(e, feed_url: str) -> FetchedItem | None:
    link = e.get("link")
    title = html_to_text(e.get("title", ""))
    if not link and not title:
        return None
    guid = e.get("id") or link or title
    ext = hashlib.sha256(f"{feed_url}|{guid}".encode()).hexdigest()[:40]

    body = ""
    if e.get("content"):
        body = max((c.get("value", "") for c in e.content), key=len)
    body = html_to_text(body or e.get("summary", ""), paragraphs=True)

    ts = e.get("published_parsed") or e.get("updated_parsed")
    published = datetime.fromtimestamp(calendar.timegm(ts), tz=UTC) if ts else datetime.now(UTC)
    published = min(published, datetime.now(UTC))

    return FetchedItem(
        external_id=f"rss:{ext}",
        url=link or feed_url,
        title=title or link,
        body=body,
        author=e.get("author"),
        discussion_url=e.get("comments"),
        published_at=published,
    )
