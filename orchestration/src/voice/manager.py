from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from src.voice.exceptions import (
    VoiceSessionExpiredError,
    VoiceSessionLimitError,
    VoiceSessionNotFoundError,
)
from src.voice.session import (
    DEFAULT_SESSION_TTL_SECONDS,
    VoiceSession,
    VoiceSessionHandles,
    new_session_id,
)

LOGGER = logging.getLogger("loki.voice.manager")

DEFAULT_MAX_CONCURRENT_SESSIONS = 1
SWEEP_INTERVAL_SECONDS = 5.0


class VoiceSessionManager:
    def __init__(
        self,
        *,
        handles_factory: Callable[[], VoiceSessionHandles],
        session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_CONCURRENT_SESSIONS,
    ):
        self._handles_factory = handles_factory
        self.session_ttl_seconds = session_ttl_seconds
        self.max_sessions = max_sessions

        self._sessions: dict[str, VoiceSession] = {}
        self._lock = threading.Lock()
        self._sweep_thread: Optional[threading.Thread] = None
        self._sweep_stop = threading.Event()

    def start_sweeper(self) -> None:
        if self._sweep_thread is not None:
            return
        self._sweep_thread = threading.Thread(
            target=self._sweep_loop, daemon=True, name="loki-voice-sweeper"
        )
        self._sweep_thread.start()

    def stop_sweeper(self) -> None:
        self._sweep_stop.set()

    def _sweep_loop(self) -> None:
        while not self._sweep_stop.wait(SWEEP_INTERVAL_SECONDS):
            self._sweep_expired()

    def _sweep_expired(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s.is_expired(now)]
        for sid in expired:
            LOGGER.info("voice: sweeping expired session %s", sid)
            self.release_session(sid)

    def create_session(
        self, *, activation_source: str = "push_to_talk", text_only: bool = False
    ) -> VoiceSession:
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                self._sweep_expired_locked()
            if len(self._sessions) >= self.max_sessions:
                raise VoiceSessionLimitError(self.max_sessions)

            session_id = new_session_id()
            handles = self._handles_factory()
            session = VoiceSession(
                session_id,
                handles,
                activation_source=activation_source,
                ttl_seconds=self.session_ttl_seconds,
                text_only=text_only,
            )
            self._sessions[session_id] = session
            return session

    def _sweep_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [sid for sid, s in self._sessions.items() if s.is_expired(now)]
        for sid in expired:
            session = self._sessions.pop(sid, None)
            if session is not None:
                session.close()

    def get_session(self, session_id: str) -> VoiceSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise VoiceSessionNotFoundError(session_id)
        if session.is_expired():
            self.release_session(session_id)
            raise VoiceSessionExpiredError(session_id)
        return session

    def cancel_session(self, session_id: str, reason: str = "cancelled") -> None:
        session = self.get_session(session_id)
        session.cancel(reason)

    def release_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            session.close()

    @property
    def active_session_count(self) -> int:
        with self._lock:
            return len(self._sessions)
