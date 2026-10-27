"""neutralise non-http(s) item and saved URLs

Feed links were stored unvalidated, so a feed could plant javascript:/data: URLs that end up
in an href. Fetchers now reject them; this cleans up rows stored before that.

Revision ID: b3e8c51f7a24
Revises: 9d4f2a6c1b85
Create Date: 2026-10-27 20:00:00.000000
"""
from alembic import op


revision = 'b3e8c51f7a24'
down_revision = '9d4f2a6c1b85'
branch_labels = None
depends_on = None


def unsafe(col: str) -> str:
    """SQL for "not an absolute http(s) URL with a host" (mirrors app.fetchers.base.safe_http_url)."""
    return f"({col} !~* '^[[:space:]]*https?://[^/?#[:space:]]' OR {col} ~ '[[:cntrl:]]')"


SQL = [
    f"UPDATE items SET discussion_url = NULL WHERE discussion_url IS NOT NULL AND {unsafe('discussion_url')}",
    # Point the item at its discussion page, else at the feed / subreddit it came from
    f"""UPDATE items SET url = discussion_url, canonical_url = discussion_url
        WHERE {unsafe('url')} AND discussion_url IS NOT NULL""",
    f"""UPDATE items i SET url = s.identifier, canonical_url = s.identifier
        FROM item_sources x JOIN sources s ON s.id = x.source_id
        WHERE x.item_id = i.id AND {unsafe('i.url')} AND s.kind = 'rss' AND NOT {unsafe('s.identifier')}""",
    f"""UPDATE items i SET url = 'https://www.reddit.com/r/' || s.identifier,
            canonical_url = 'https://reddit.com/r/' || s.identifier
        FROM item_sources x JOIN sources s ON s.id = x.source_id
        WHERE x.item_id = i.id AND {unsafe('i.url')} AND s.kind = 'reddit'""",
    f"DELETE FROM items WHERE {unsafe('url')}",
    # Saved copies: take the (now safe) item URL, unless the user already saved that URL
    # (or another of their unsafe rows gets it too), so the (user_id, url) constraint holds
    f"""DELETE FROM saved s USING items i
        WHERE {unsafe('s.url')} AND s.item_id = i.id AND EXISTS (
            SELECT 1 FROM saved o LEFT JOIN items oi ON oi.id = o.item_id
            WHERE o.user_id = s.user_id AND o.id <> s.id
              AND ((o.url = i.url AND NOT {unsafe('o.url')}) OR ({unsafe('o.url')} AND oi.url = i.url AND o.id < s.id)))""",
    f"UPDATE saved s SET url = i.url FROM items i WHERE {unsafe('s.url')} AND s.item_id = i.id",
    f"DELETE FROM saved WHERE {unsafe('url')}",
]


def upgrade() -> None:
    for stmt in SQL:
        op.execute(stmt)


def downgrade() -> None:
    pass  # the old values were unsafe; nothing to restore
