"""Reddit fetcher.

Uses application-only OAuth against oauth.reddit.com, so REDDIT_CLIENT_ID/SECRET are
required. Reddit shut down its public .rss feeds on 2026-11-13 (they answer 404 now) and
blocks unauthenticated JSON, so there is no credential-free way to read a subreddit.
"""

import logging
import time
from datetime import UTC, datetime

import httpx

from app.config import Settings
from app.fetchers.base import FetchedItem, FetchResult, get_with_backoff, safe_http_url
from app.fetchers.net import fetch_client

log = logging.getLogger(__name__)

PUBLIC_BASE = "https://www.reddit.com"
OAUTH_BASE = "https://oauth.reddit.com"
COMMENT_CHAR_LIMIT = 4000  # the LLM prompt has its own, smaller budget
NO_CREDENTIALS = (
    "Reddit shut down its public RSS feeds (13 Nov 2026). Subreddits now need Reddit API "
    "credentials: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env."
)


class RedditCredentialsRequired(httpx.HTTPError):
    """No API credentials configured; raised before any request is sent."""

    def __init__(self) -> None:
        super().__init__(NO_CREDENTIALS)


class RedditFetcher:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or fetch_client(
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
            raise RedditCredentialsRequired
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
        data = await self._get_json(f"/r/{subreddit}/new", {"limit": 50})
        items = [parse_post(child["data"]) for child in data["data"]["children"] if child.get("kind") == "t3"]
        return FetchResult(items=[i for i in items if i is not None])

    async def fetch_comments(self, post_id: str, top_n: int) -> str:
        data = await self._get_json(f"/comments/{post_id}", {"sort": "top", "limit": top_n, "depth": 1})
        return parse_comments(data, top_n)


def parse_post(d: dict) -> FetchedItem | None:
    path = d.get("permalink") or ""
    # "/r/..." only: "@evil.example/" or "//evil.example" would move the link off reddit
    if not path.startswith("/") or path.startswith("//"):
        return None
    permalink = safe_http_url(f"https://www.reddit.com{path}")
    if not permalink:
        return None
    is_self = d.get("is_self", False)
    url = permalink if is_self else (safe_http_url(d.get("url_overridden_by_dest") or d.get("url")) or permalink)
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
