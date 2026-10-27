"""Which build of Feedler is running: package version plus the commit and date baked in by CI."""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version

REPO_URL = "https://github.com/olizimmermann/feedler"


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str | None  # full SHA, None for local or source builds
    released_at: datetime | None  # commit time of the build

    @property
    def short_commit(self) -> str | None:
        return self.commit[:7] if self.commit else None

    @property
    def commit_url(self) -> str | None:
        return f"{REPO_URL}/commit/{self.commit}" if self.commit else None

    @property
    def label(self) -> str:
        return f"{self.version} ({self.short_commit})" if self.commit else f"{self.version} (dev)"


@lru_cache
def build_info() -> BuildInfo:
    try:
        pkg_version = version("feedler")
    except PackageNotFoundError:
        pkg_version = "0.0.0"
    # Set from Docker build args; validated because they end up in links
    commit = os.environ.get("FEEDLER_COMMIT", "").strip().lower()
    commit = commit if re.fullmatch(r"[0-9a-f]{7,40}", commit) else None
    try:
        released_at = datetime.fromisoformat(os.environ.get("FEEDLER_RELEASED_AT", "").strip())
    except ValueError:
        released_at = None
    if released_at and released_at.tzinfo is None:
        released_at = None
    return BuildInfo(pkg_version, commit, released_at)
