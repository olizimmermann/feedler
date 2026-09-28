import asyncio
import logging

import httpx
import trafilatura

log = logging.getLogger(__name__)

MAX_HTML_BYTES = 5_000_000


async def fetch_html(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=20, follow_redirects=True)
        if resp.status_code != 200 or "html" not in resp.headers.get("content-type", "html"):
            return None
        return resp.text[:MAX_HTML_BYTES]
    except httpx.HTTPError as e:
        log.info("article fetch failed for %s: %s", url, e)
        return None


async def extract_article(client: httpx.AsyncClient, url: str) -> str | None:
    """Download a page and return its main text content (or None)."""
    html = await fetch_html(client, url)
    if not html:
        return None
    return await asyncio.to_thread(trafilatura.extract, html, include_comments=False, include_tables=True)


async def extract_title_and_text(client: httpx.AsyncClient, url: str) -> tuple[str | None, str | None]:
    html = await fetch_html(client, url)
    if not html:
        return None, None

    def _run():
        meta = trafilatura.extract_metadata(html)
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        return (meta.title if meta else None), text

    return await asyncio.to_thread(_run)
