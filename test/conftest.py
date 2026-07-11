"""test/conftest.py — Fixtures pytest"""
import asyncio

import aiosqlite
import pytest
import pytest_asyncio

import database


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db(monkeypatch, tmp_path):
    test_db = tmp_path / "test_lifesim.db"
    monkeypatch.setattr(database, "DB_PATH", str(test_db))
    await database.init_db()
    conn = await aiosqlite.connect(str(test_db))
    conn.row_factory = aiosqlite.Row
    yield conn
    await conn.close()


@pytest.fixture
def fake_user():
    return {"id": 123456789, "username": "testeur", "first_name": "Test"}