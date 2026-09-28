from app.fetchers.base import canonicalize_url
from app.fetchers.reddit import parse_comments, parse_post, parse_rss
from app.fetchers.rss import parse_entry
from app.llm.base import LLMError, parse_json
from app.llm.prompts import InterestSpec, PostSpec, build_classify_prompt
from app.routers.sources import parse_source

import feedparser
import pytest


def test_reddit_public_rss(fixture_bytes):
    items = parse_rss(fixture_bytes("reddit_prusa3d.rss"))
    assert len(items) == 10
    first = items[0]
    assert first.external_id.startswith("reddit:t3_")
    assert first.discussion_url.startswith("https://www.reddit.com/r/prusa3d/comments/")
    assert "submitted by" not in first.body
    assert first.author and not first.author.startswith("/u/")
    assert first.published_at.tzinfo is not None


def test_reddit_json_post_and_comments():
    link_post = parse_post({
        "name": "t3_abc", "id": "abc", "permalink": "/r/prusa3d/comments/abc/x/", "is_self": False,
        "url_overridden_by_dest": "https://blog.prusa3d.com/fix/?utm_source=reddit", "title": " Fix ",
        "selftext": "", "author": "bob", "score": 12, "num_comments": 3, "created_utc": 1_700_000_000,
    })
    assert link_post.url == "https://blog.prusa3d.com/fix/?utm_source=reddit"
    assert link_post.discussion_url == "https://www.reddit.com/r/prusa3d/comments/abc/x/"
    assert link_post.title == "Fix" and link_post.body.startswith("[link post")

    comments = parse_comments(
        [{}, {"data": {"children": [
            {"kind": "t1", "data": {"body": "pinned", "stickied": True, "score": 1, "author": "mod"}},
            {"kind": "t1", "data": {"body": "Update  the\nfirmware", "score": 40, "author": "a"}},
            {"kind": "t1", "data": {"body": "[deleted]", "score": 5, "author": "b"}},
            {"kind": "more", "data": {}},
        ]}}],
        top_n=5,
    )
    assert comments == "[+40] u/a: Update the firmware"


def test_rss_entry():
    feed = feedparser.parse(
        b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
        <item><title>Hello &amp; bye</title><link>https://ex.com/a</link><guid>g1</guid>
        <description>&lt;p&gt;Some &lt;b&gt;text&lt;/b&gt;&lt;/p&gt;</description>
        <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item></channel></rss>"""
    )
    item = parse_entry(feed.entries[0], "https://ex.com/feed")
    assert item.title == "Hello & bye"
    assert item.body == "Some text"
    assert item.external_id.startswith("rss:")
    assert item.published_at.year == 2024


def test_canonicalize_url():
    a = canonicalize_url("http://www.Example.com/post/?utm_source=x&b=2&a=1#frag")
    b = canonicalize_url("https://example.com/post?a=1&b=2")
    assert a == b == "https://example.com/post?a=1&b=2"


@pytest.mark.parametrize("raw,expected", [
    ("r/prusa3d", ("reddit", "prusa3d")),
    ("prusa3d", ("reddit", "prusa3d")),
    ("https://www.reddit.com/r/Prusa3D/", ("reddit", "prusa3d")),
    ("https://blog.prusa3d.com/feed/", ("rss", "https://blog.prusa3d.com/feed/")),
    ("hnrss.org/newest", ("rss", "https://hnrss.org/newest")),
    ("not valid at all", None),
])
def test_parse_source(raw, expected):
    assert parse_source(raw) == expected


def test_parse_json_lenient():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    with pytest.raises(LLMError):
        parse_json("no json here")


def test_classify_prompt_scoping_and_truncation():
    prompt = build_classify_prompt(
        [InterestSpec(1, "Bugs", "firmware bugs", "r/prusa3d"), InterestSpec(2, "News", "")],
        None,
        [PostSpec(7, "r/prusa3d", "Title", "x" * 5000, comments="c" * 5000)],
        char_budget=1000,
    )
    assert "[id 1] Bugs (only for posts from r/prusa3d): firmware bugs" in prompt
    assert "[id 2] News" in prompt
    assert '<post id="7" source="r/prusa3d">' in prompt
    assert "(none yet)" in prompt
    assert len(prompt) < 1500
