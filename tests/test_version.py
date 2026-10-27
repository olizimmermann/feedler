from datetime import UTC, datetime

import pytest

from app.version import build_info


@pytest.fixture
def info(monkeypatch):
    def make(commit="", released_at=""):
        monkeypatch.setenv("FEEDLER_COMMIT", commit)
        monkeypatch.setenv("FEEDLER_RELEASED_AT", released_at)
        build_info.cache_clear()
        return build_info()

    yield make
    build_info.cache_clear()


def test_build_info_from_ci(info):
    b = info("9BFA2D39412B25C24F5B58444E087E581E70042A", "2026-10-27T18:02:11+00:00")
    assert b.commit == "9bfa2d39412b25c24f5b58444e087e581e70042a" and b.short_commit == "9bfa2d3"
    assert b.commit_url == "https://github.com/olizimmermann/feedler/commit/9bfa2d39412b25c24f5b58444e087e581e70042a"
    assert b.released_at == datetime(2026, 10, 27, 18, 2, 11, tzinfo=UTC)
    assert b.label.endswith("(9bfa2d3)")
    # Git gives the committer's offset (as CI does); shown in UTC
    assert info(b.commit, "2026-10-27T21:19:33+01:00").released_at.isoformat() == "2026-10-27T20:19:33+00:00"


def test_build_info_rejects_junk(info):
    # Values end up in links, so anything that isn't a hex SHA or an aware timestamp is dropped
    b = info('"><script>x</script>', "yesterday")
    assert b.commit is None and b.commit_url is None and b.released_at is None
    assert b.label.endswith("(dev)") and b.version
    assert info("abc", "2026-10-27T18:02:11").commit is None  # too short, and naive time is dropped
    assert info("abc", "2026-10-27T18:02:11").released_at is None
