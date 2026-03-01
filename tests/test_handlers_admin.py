import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

import handlers


class DummyMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))


class DummyUser:
    def __init__(self, user_id):
        self.id = user_id


class DummyUpdate:
    def __init__(self, user_id):
        self.effective_user = DummyUser(user_id)
        self.message = DummyMessage()


class DummyContext:
    def __init__(self, args=None):
        self.args = args or []


@pytest.mark.asyncio
async def test_broadcast_unauthorized_gets_feedback(monkeypatch):
    monkeypatch.setattr(handlers, "ADMIN_ID", "999")
    update = DummyUpdate(user_id=123)
    context = DummyContext(args=["hello"])

    await handlers.broadcast_command(update, context)

    assert update.message.calls
    assert "Unauthorized" in update.message.calls[0][0]


@pytest.mark.asyncio
async def test_users_unauthorized_gets_feedback(monkeypatch):
    monkeypatch.setattr(handlers, "ADMIN_ID", "999")
    update = DummyUpdate(user_id=123)
    context = DummyContext()

    await handlers.users_command(update, context)

    assert update.message.calls
    assert "Unauthorized" in update.message.calls[0][0]
