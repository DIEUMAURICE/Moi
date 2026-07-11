"""test/test_smoke.py — Tests de fumée"""
import importlib

import pytest


def test_import_bot():
    import bot  # noqa: F401


def test_build_app():
    import bot
    app = bot.build_app()
    assert app is not None
    assert len(app.handlers) > 0


@pytest.mark.parametrize("module", [
    "handlers.general", "handlers.economy", "handlers.bank",
    "handlers.profile", "handlers.casino", "handlers.companies",
    "handlers.social", "handlers.political", "handlers.multiplayer",
])
def test_handler_modules_import(module):
    importlib.import_module(module)


@pytest.mark.asyncio
async def test_db_init(db):
    async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
        tables = await cur.fetchall()
    assert len(tables) > 0