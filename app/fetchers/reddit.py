"""Reddit fetcher.

With REDDIT_CLIENT_ID/SECRET set, uses application-only OAuth against oauth.reddit.com
(higher rate limits, scores, comments). Otherwise falls back to the public RSS feed
(reddit blocks unauthenticated JSON requests) and skips comments.
"""

import calendar
import logging
import re
import time
from datetime import UTC, datetime

import feedparser
import httpx

from app.config import Settings
from app.fetchers.base import FetchedItem, FetchResult, get_with_backoff
from app.fetchers.rss import html_to_text

log = logging.getLogger(__name__)

PUBLIC_BASE = "https://www.reddit.com"
OAUTH_BASE = "https://oauth.reddit.com"
COMMENT_CHAR_LIMIT = 1000


class RedditFetcher:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=30, headers={"User-Agent": settings.reddit_user_agent}, follow_redirects=True
        )
        self._token: str | None = None
        self._token_expires = 0.0

    @property
    def oauth(self) -> bool:
        return self.settings.reddit_oauth

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _auth_headers(self) -> dict[str, str]:
        if not self.oauth:
            return {}
        if not self._token or time.time() > self._token_expires - 60:
            resp = await self.client.post(
                f"{PUBLIC_BASE}/api/v1/access_token",
                auth=(self.settings.reddit_client_id, self.settings.reddit_client_secret),
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires = time.time() + int(data.get("expires_in", 3600))
        return {"Authorization": f"bearer {self._token}"}

    async def _get_json(self, path: str, params: dict) -> dict | list:
        resp = await get_with_backoff(
            self.client, f"{OAUTH_BASE}{path}", params={**params, "raw_json": 1}, headers=await self._auth_headers()
        )
        resp.raise_for_status()
        return resp.json()

    async def fetch(self, subreddit: str) -> FetchResult:
        if not self.oauth:
            resp = await get_with_backoff(self.client, f"{PUBLIC_BASE}/r/{subreddit}/new/.rss", params={"limit": 50})
            resp.raise_for_status()
            return FetchResult(items=parse_rss(resp.content))
        data = await self._get_json(f"/r/{subreddit}/new", {"limit": 50})
        items = [parse_post(child["data"]) for child in data["data"]["children"] if child.get("kind") == "t3"]
        return FetchResult(items=items)

    async def fetch_comments(self, post_id: str, top_n: int) -> str:
        data = await self._get_json(f"/comments/{post_id}", {"sort": "top", "limit": top_n, "depth": 1})
        return parse_comments(data, top_n)


def parse_post(d: dict) -> FetchedItem:
    permalink = f"https://www.reddit.com{d['permalink']}"
    is_self = d.get("is_self", False)
    url = permalink if is_self else (d.get("url_overridden_by_dest") or d.get("url") or permalink)
    body = d.get("selftext") or ""
    if not is_self and not body:
        body = f"[link post to {url}]"
    return FetchedItem(
        external_id=f"reddit:{d['name']}",
        url=url,
        discussion_url=permalink,
        title=d.get("title", "").strip(),
        body=body,
        author=d.get("author"),
        score=d.get("score"),
        num_comments=d.get("num_comments"),
        published_at=datetime.fromtimestamp(d.get("created_utc", time.time()), tz=UTC),
        extra={"id": d["id"], "subreddit": d.get("subreddit"), "flair": d.get("link_flair_text")},
    )


def parse_comments(data: list, top_n: int) -> str:
    if not isinstance(data, list) or len(data) < 2:
        return ""
    lines = []
    for child in data[1]["data"]["children"]:
        if child.get("kind") != "t1":
            continue
        c = child["data"]
        if c.get("stickied") or c.get("body") in ("[deleted]", "[removed]"):
            continue
        text = " ".join(c.get("body", "").split())[:COMMENT_CHAR_LIMIT]
        lines.append(f"[{c.get('score', 0):+d}] u/{c.get('author', '?')}: {text}")
        if len(lines) >= top_n:
            break
    return "\n".join(lines)


_TRAILER_RE = re.compile(r"&#32;\s*submitted by.*$", re.DOTALL)
_LINK_RE = re.compile(r'<a href="([^"]+)">\[link\]</a>')


def parse_rss(content: bytes) -> list[FetchedItem]:
    """Parse reddit's public /new/.rss feed (no scores or comment counts there)."""
    items = []
    for e in feedparser.parse(content).entries:
        post_id = e.get("id", "")
        if not post_id.startswith("t3_"):
            continue
        html = e.content[0].value if e.get("content") else e.get("summary", "")
        link = _LINK_RE.search(html)
        permalink = e.get("link")
        url = link.group(1) if link else permalink
        body = html_to_text(_TRAILER_RE.sub("", html))
        if url != permalink and not body:
            body = f"[link post to {url}]"
        ts = e.get("published_parsed") or e.get("updated_parsed")
        items.append(
            FetchedItem(
                external_id=f"reddit:{post_id}",
                url=url,
                discussion_url=permalink,
                title=e.get("title", "").strip(),
                body=body,
                author=(e.get("author") or "").removeprefix("/u/") or None,
                published_at=datetime.fromtimestamp(calendar.timegm(ts), tz=UTC) if ts else datetime.now(UTC),
                extra={"id": post_id.removeprefix("t3_")},
            )
        )
    return items
