"""Session persistence: PTY sessions that survive WebSocket disconnects.

Each Session owns a PTY process and a scrollback ring buffer. The PTY read
loop runs continuously, feeding the scrollback regardless of whether any
WebSocket client is attached. On reconnect, the client receives the
scrollback contents before live data.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from aiohttp import web
from loguru import logger
from winpty import PtyProcess


class ScrollbackBuffer:
    """Fixed-size ring buffer for raw PTY output bytes."""

    def __init__(self, capacity: int = 65536):
        self._buf = bytearray(capacity)
        self._capacity = capacity
        self._write_pos = 0
        self._total_written = 0

    @property
    def total_written(self) -> int:
        return self._total_written

    @property
    def size(self) -> int:
        return min(self._total_written, self._capacity)

    def append(self, data: bytes) -> None:
        """Append data to the ring buffer."""
        n = len(data)
        if n == 0:
            return
        if n >= self._capacity:
            # Data larger than buffer — keep only the tail
            data = data[-self._capacity:]
            n = self._capacity
            self._buf[:] = data
            self._write_pos = 0
            self._total_written += len(data)
            return

        end = self._write_pos + n
        if end <= self._capacity:
            self._buf[self._write_pos:end] = data
        else:
            first = self._capacity - self._write_pos
            self._buf[self._write_pos:] = data[:first]
            self._buf[:n - first] = data[first:]
        self._write_pos = end % self._capacity
        self._total_written += n

    def get_all(self) -> bytes:
        """Return all buffered data in order."""
        if self._total_written == 0:
            return b""
        if self._total_written < self._capacity:
            return bytes(self._buf[:self._write_pos])
        # Buffer is full or has wrapped
        if self._write_pos == 0:
            return bytes(self._buf[:self._capacity])
        return bytes(self._buf[self._write_pos:] + self._buf[:self._write_pos])


@dataclass
class Session:
    """A persistent PTY session."""
    session_id: str
    cmd: str
    hub: Optional[str]
    pty: PtyProcess
    scrollback: ScrollbackBuffer
    created_at: float = field(default_factory=time.monotonic)
    last_client_time: float = field(default_factory=time.monotonic)
    clients: set = field(default_factory=set)
    read_task: Optional[asyncio.Task] = None
    alive: bool = True
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionManager:
    """Registry of persistent PTY sessions."""

    def __init__(self, orphan_timeout: float = 300, scrollback_size: int = 65536):
        self._sessions: dict[str, Session] = {}
        self.orphan_timeout = orphan_timeout
        self.scrollback_size = scrollback_size
        self._reaper_task: Optional[asyncio.Task] = None

    def create_session(
        self,
        cmd: str,
        hub: str | None = None,
        dimensions: tuple[int, int] = (24, 80),
    ) -> Session:
        """Spawn a PTY and register a new session."""
        session_id = uuid.uuid4().hex[:16]
        logger.info("Creating session {} for cmd={!r} hub={}", session_id, cmd, hub)

        pty = PtyProcess.spawn(cmd, dimensions=dimensions)

        session = Session(
            session_id=session_id,
            cmd=cmd,
            hub=hub,
            pty=pty,
            scrollback=ScrollbackBuffer(self.scrollback_size),
        )
        session.read_task = asyncio.create_task(self._pty_read_loop(session))
        self._sessions[session_id] = session

        logger.info("Session {} created (PTY spawned)", session_id)
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def attach(self, session_id: str, ws: web.WebSocketResponse) -> bytes | None:
        """Attach a WebSocket client to a session. Returns scrollback for replay."""
        session = self._sessions.get(session_id)
        if not session or not session.alive:
            return None

        async with session._lock:
            scrollback = session.scrollback.get_all()
            session.clients.add(ws)
            session.last_client_time = time.monotonic()

        logger.info("Session {}: client attached ({} total), scrollback={} bytes",
                     session_id, len(session.clients), len(scrollback))
        return scrollback

    def detach(self, session_id: str, ws: web.WebSocketResponse) -> None:
        """Detach a WebSocket client from a session."""
        session = self._sessions.get(session_id)
        if not session:
            return
        session.clients.discard(ws)
        session.last_client_time = time.monotonic()
        logger.info("Session {}: client detached ({} remaining)",
                     session_id, len(session.clients))

    def destroy_session(self, session_id: str) -> None:
        """Kill a session's PTY and remove it from the registry."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        session.alive = False
        if session.read_task:
            session.read_task.cancel()
        try:
            session.pty.terminate(force=True)
        except Exception:
            pass
        logger.info("Session {} destroyed", session_id)

    def list_sessions(self) -> list[dict]:
        """Return metadata for all active sessions."""
        now = time.monotonic()
        return [
            {
                "session_id": s.session_id,
                "cmd": s.cmd,
                "hub": s.hub,
                "alive": s.alive,
                "client_count": len(s.clients),
                "scrollback_size": s.scrollback.size,
                "age_seconds": int(now - s.created_at),
                "idle_seconds": int(now - s.last_client_time),
            }
            for s in self._sessions.values()
        ]

    async def _pty_read_loop(self, session: Session) -> None:
        """Continuously read PTY output into scrollback and fan out to clients."""
        loop = asyncio.get_event_loop()
        logger.debug("Session {}: read loop started", session.session_id)
        try:
            while session.pty.isalive() and session.alive:
                try:
                    data = await loop.run_in_executor(None, session.pty.read)
                    if not data:
                        continue
                    raw = data.encode("utf-8", errors="replace")

                    async with session._lock:
                        session.scrollback.append(raw)
                        dead = []
                        for ws in session.clients:
                            try:
                                await ws.send_bytes(raw)
                            except Exception:
                                dead.append(ws)
                        for ws in dead:
                            session.clients.discard(ws)
                except EOFError:
                    logger.info("Session {}: PTY EOF", session.session_id)
                    break
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("Session {}: read error", session.session_id)
                    break
        finally:
            session.alive = False
            logger.info("Session {}: read loop ended", session.session_id)

    def start_orphan_reaper(self) -> None:
        """Start background task to clean up orphaned sessions."""
        self._reaper_task = asyncio.create_task(self._orphan_reaper())

    async def _orphan_reaper(self) -> None:
        """Periodically kill sessions with no clients past the timeout."""
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            to_kill = [
                sid for sid, s in self._sessions.items()
                if len(s.clients) == 0
                and (now - s.last_client_time) > self.orphan_timeout
            ]
            for sid in to_kill:
                logger.info("Orphan reaper: killing session {} (no clients for {}s)",
                            sid, int(now - self._sessions[sid].last_client_time))
                self.destroy_session(sid)

    def stop_all(self) -> None:
        """Destroy all sessions and stop the reaper."""
        if self._reaper_task:
            self._reaper_task.cancel()
        for sid in list(self._sessions.keys()):
            self.destroy_session(sid)
        logger.info("SessionManager: all sessions stopped")
