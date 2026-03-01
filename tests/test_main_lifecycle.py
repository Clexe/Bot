import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

import main


def test_validate_env_missing_deriv_exits(monkeypatch):
    monkeypatch.setattr(main, "BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DATABASE_URL", "postgres://example")
    monkeypatch.setattr(main, "DERIV_APP_ID", "")
    monkeypatch.setattr(main, "DERIV_TOKEN", "")

    with pytest.raises(SystemExit):
        main._validate_env()


def test_validate_env_warns_when_admin_missing(monkeypatch):
    monkeypatch.setattr(main, "BOT_TOKEN", "token")
    monkeypatch.setattr(main, "DATABASE_URL", "postgres://example")
    monkeypatch.setattr(main, "DERIV_APP_ID", "123")
    monkeypatch.setattr(main, "DERIV_TOKEN", "secret")
    monkeypatch.setattr(main, "ADMIN_ID", "")

    called = {"warn": False}

    def _warn(*args, **kwargs):
        called["warn"] = True

    monkeypatch.setattr(main.logger, "warning", _warn)
    main._validate_env()
    assert called["warn"] is True


@pytest.mark.asyncio
async def test_post_shutdown_closes_fetchers_and_pool(monkeypatch):
    class DummyTask:
        def done(self):
            return True

    main._scanner_task = DummyTask()

    called = {"fetchers": False, "pool": False}

    async def _shutdown_fetchers():
        called["fetchers"] = True

    def _close_pool():
        called["pool"] = True

    monkeypatch.setattr(main, "shutdown_fetchers", _shutdown_fetchers)
    monkeypatch.setattr(main, "close_pool", _close_pool)

    await main.post_shutdown(app=None)

    assert called["fetchers"] is True
    assert called["pool"] is True
