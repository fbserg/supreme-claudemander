"""Tests for session persistence: ScrollbackBuffer, SessionManager, and session APIs."""

import asyncio
import json
import time
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from aiohttp import web

from claude_rts.sessions import ScrollbackBuffer, SessionManager, Session
from claude_rts.server import create_app


# ── ScrollbackBuffer unit tests ─────────────────────────────────────────────


def test_scrollback_empty():
    buf = ScrollbackBuffer(1024)
    assert buf.get_all() == b""
    assert buf.size == 0
    assert buf.total_written == 0


def test_scrollback_basic_write():
    buf = ScrollbackBuffer(1024)
    buf.append(b"hello")
    assert buf.get_all() == b"hello"
    assert buf.size == 5
    assert buf.total_written == 5


def test_scrollback_multiple_writes():
    buf = ScrollbackBuffer(1024)
    buf.append(b"hello ")
    buf.append(b"world")
    assert buf.get_all() == b"hello world"
    assert buf.total_written == 11


def test_scrollback_wraparound():
    buf = ScrollbackBuffer(10)
    buf.append(b"12345")
    buf.append(b"67890")
    assert buf.get_all() == b"1234567890"
    # Now overflow
    buf.append(b"ABC")
    result = buf.get_all()
    assert len(result) == 10
    assert result == b"4567890ABC"


def test_scrollback_large_write_exceeds_capacity():
    buf = ScrollbackBuffer(8)
    buf.append(b"0123456789ABCDEF")
    # Only last 8 bytes kept
    assert buf.get_all() == b"89ABCDEF"
    assert buf.size == 8


def test_scrollback_empty_append():
    buf = ScrollbackBuffer(1024)
    buf.append(b"data")
    buf.append(b"")
    assert buf.get_all() == b"data"


# ── SessionManager unit tests (mocked PTY) ──────────────────────────────────


class MockPty:
    """Mock PtyProcess for testing."""
    def __init__(self):
        self._alive = True
        self._output_queue = []
        self._written = []

    def isalive(self):
        return self._alive

    def read(self):
        if self._output_queue:
            return self._output_queue.pop(0)
        # Simulate blocking read that returns when killed
        time.sleep(0.1)
        if not self._alive:
            raise EOFError()
        return ""

    def write(self, text):
        self._written.append(text)

    def setwinsize(self, rows, cols):
        pass

    def terminate(self, force=False):
        self._alive = False

    @classmethod
    def spawn(cls, cmd, dimensions=(24, 80)):
        return cls()


async def test_session_manager_create(monkeypatch):
    monkeypatch.setattr("claude_rts.sessions.PtyProcess", MockPty)
    mgr = SessionManager()
    session = mgr.create_session("echo hello")
    assert session.session_id
    assert session.cmd == "echo hello"
    assert session.alive
    assert mgr.get_session(session.session_id) is session
    mgr.stop_all()


async def test_session_manager_destroy(monkeypatch):
    monkeypatch.setattr("claude_rts.sessions.PtyProcess", MockPty)
    mgr = SessionManager()
    session = mgr.create_session("test")
    sid = session.session_id
    mgr.destroy_session(sid)
    assert mgr.get_session(sid) is None
    assert not session.alive
    mgr.stop_all()


async def test_session_manager_list(monkeypatch):
    monkeypatch.setattr("claude_rts.sessions.PtyProcess", MockPty)
    mgr = SessionManager()
    mgr.create_session("cmd1", hub="hub1")
    mgr.create_session("cmd2", hub="hub2")
    sessions = mgr.list_sessions()
    assert len(sessions) == 2
    hubs = {s["hub"] for s in sessions}
    assert hubs == {"hub1", "hub2"}
    mgr.stop_all()


async def test_session_manager_stop_all(monkeypatch):
    monkeypatch.setattr("claude_rts.sessions.PtyProcess", MockPty)
    mgr = SessionManager()
    s1 = mgr.create_session("cmd1")
    s2 = mgr.create_session("cmd2")
    mgr.stop_all()
    assert mgr.get_session(s1.session_id) is None
    assert mgr.get_session(s2.session_id) is None


# ── Test puppeting API tests ─────────────────────────────────────────────────


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr("claude_rts.sessions.PtyProcess", MockPty)
    return create_app(test_mode=True)


@pytest.fixture
async def client(aiohttp_client, app):
    return await aiohttp_client(app)


async def test_test_session_create(client):
    resp = await client.post("/api/test/session/create?cmd=echo+hello")
    assert resp.status == 200
    data = await resp.json()
    assert "session_id" in data


async def test_test_session_status(client):
    resp = await client.post("/api/test/session/create?cmd=echo+hello")
    data = await resp.json()
    sid = data["session_id"]

    resp = await client.get(f"/api/test/session/{sid}/status")
    assert resp.status == 200
    status = await resp.json()
    assert status["session_id"] == sid
    assert status["alive"] is True
    assert status["client_count"] == 0


async def test_test_session_send_and_read(client):
    resp = await client.post("/api/test/session/create?cmd=echo+hello")
    data = await resp.json()
    sid = data["session_id"]

    # Send text to PTY
    resp = await client.post(f"/api/test/session/{sid}/send", data="test input")
    assert resp.status == 200

    # Read scrollback (may be empty since MockPty doesn't echo)
    resp = await client.get(f"/api/test/session/{sid}/read")
    assert resp.status == 200
    read_data = await resp.json()
    assert "output" in read_data
    assert "size" in read_data


async def test_test_session_delete(client):
    resp = await client.post("/api/test/session/create?cmd=echo+hello")
    data = await resp.json()
    sid = data["session_id"]

    resp = await client.delete(f"/api/test/session/{sid}")
    assert resp.status == 200

    resp = await client.get(f"/api/test/session/{sid}/status")
    assert resp.status == 404


async def test_test_session_not_found(client):
    resp = await client.get("/api/test/session/nonexistent/status")
    assert resp.status == 404


async def test_test_sessions_list(client):
    await client.post("/api/test/session/create?cmd=cmd1")
    await client.post("/api/test/session/create?cmd=cmd2")
    resp = await client.get("/api/test/sessions")
    assert resp.status == 200
    data = await resp.json()
    assert len(data) >= 2


async def test_sessions_list_api(client):
    """The non-test sessions list endpoint should also work."""
    resp = await client.get("/api/sessions")
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)


async def test_test_mode_disabled():
    """Test API should NOT be available when test_mode=False."""
    with patch("claude_rts.sessions.PtyProcess", MockPty):
        app = create_app(test_mode=False)
    routes = [r.resource.canonical for r in app.router.routes() if hasattr(r, 'resource')]
    assert "/api/test/sessions" not in routes
    assert "/api/test/session/{id}/read" not in routes


async def test_app_has_session_routes():
    """Verify session WebSocket routes are registered."""
    with patch("claude_rts.sessions.PtyProcess", MockPty):
        app = create_app(test_mode=False)
    routes = [r.resource.canonical for r in app.router.routes() if hasattr(r, 'resource')]
    assert "/ws/session/new" in routes
    assert "/ws/session/{session_id}" in routes
    assert "/api/sessions" in routes
