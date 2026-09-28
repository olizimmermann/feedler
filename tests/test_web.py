import re
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.db import get_db
from app.main import app
from app.models import Evaluation, Item, ItemSource, SavedItem, Source, User

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
async def client_factory(sessionmaker):
    async def override_db():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    clients = []

    async def make():
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return c

    yield make
    for c in clients:
        await c.aclose()
    app.dependency_overrides.clear()


async def csrf(client, path="/login") -> str:
    return CSRF_RE.search((await client.get(path)).text).group(1)


async def register(client, email):
    token = await csrf(client, "/register")
    r = await client.post("/register", data={"csrf_token": token, "email": email, "password": "password123"})
    assert r.status_code == 303, r.text
    return await csrf(client, "/settings/sources")


async def test_auth_csrf_and_isolation(client_factory, sessionmaker, monkeypatch):
    archived = []

    async def fake_archive(saved_id, fallback_text=None):  # the real one uses the app DB and the network
        archived.append(saved_id)

    monkeypatch.setattr("app.routers.feed.archive_saved_item", fake_archive)
    alice, bob = await client_factory(), await client_factory()
    assert (await alice.get("/")).status_code == 303  # not logged in

    a_token = await register(alice, "alice@example.com")
    b_token = await register(bob, "bob@example.com")

    # CSRF is enforced on writes
    assert (await alice.post("/settings/sources", data={"value": "r/prusa3d"})).status_code == 403
    r = await alice.post("/settings/sources", data={"csrf_token": a_token, "value": "r/prusa3d"})
    assert r.status_code == 303

    async with sessionmaker() as db:
        a = await db.scalar(select(User).where(User.email == "alice@example.com"))
        b = await db.scalar(select(User).where(User.email == "bob@example.com"))
        assert a.is_admin and not b.is_admin
        source = await db.scalar(select(Source))
        item = Item(external_id="reddit:t3_z", url="https://ex.com/a", canonical_url="https://ex.com/a",
                    title="Alice only post", published_at=datetime.now(UTC))
        db.add(item)
        await db.flush()
        db.add(ItemSource(item_id=item.id, source_id=source.id))
        db.add(Evaluation(item_id=item.id, user_id=a.id, relevance=90, reason="r", summary="s", model="m"))
        await db.commit()
        item_id = item.id

    assert "Alice only post" in (await alice.get("/")).text
    assert "Alice only post" not in (await bob.get("/")).text
    assert (await bob.get(f"/items/{item_id}")).status_code == 404
    assert (await bob.get("/admin")).status_code == 403

    # Voting and saving
    r = await alice.post(f"/items/{item_id}/vote", data={"value": 1}, headers={"X-CSRF-Token": a_token})
    assert r.status_code == 200 and "vote up on" in r.text
    r = await alice.post(f"/items/{item_id}/save", headers={"X-CSRF-Token": a_token})
    assert "★ Saved" in r.text
    async with sessionmaker() as db:
        saved = await db.scalar(select(SavedItem))
        assert saved.user_id == a.id and archived == [saved.id]
        a = await db.get(User, a.id)
        assert a.settings.votes_since_refine == 1

    # Bob can't touch Alice's saved item
    r = await bob.post(f"/saved/{saved.id}/delete", headers={"X-CSRF-Token": b_token})
    assert r.status_code == 404
    assert "ex.com" not in (await bob.get("/saved")).text

    # Output feed is reachable by token only
    r = await alice.get(f"/u/{a.feed_token}/feed.xml")
    assert r.status_code == 200 and "Alice only post" in r.text
    assert (await bob.get("/u/wrong/feed.xml")).status_code == 404


async def test_registration_can_be_closed(client_factory):
    admin, other = await client_factory(), await client_factory()
    token = await register(admin, "admin@example.com")
    r = await admin.post("/admin/registration", data={"csrf_token": token})
    assert r.status_code == 303
    token = await csrf(other, "/login")
    r = await other.post("/register", data={"csrf_token": token, "email": "x@example.com", "password": "password123"})
    assert "Registration is closed" in r.text
