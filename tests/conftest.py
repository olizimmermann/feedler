import os
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"


def _test_db_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    base, _, _ = get_settings().database_url.rpartition("/")
    return f"{base}/feedler_test"


@pytest.fixture(scope="session")
async def engine():
    from app.models import Base

    engine = create_async_engine(_test_db_url())
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except OSError as e:
        pytest.skip(f"test database not reachable: {e}")
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessionmaker(engine):
    from app.models import Base

    async with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db(sessionmaker):
    async with sessionmaker() as session:
        yield session


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()
