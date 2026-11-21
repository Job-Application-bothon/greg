"""Thread-safe per-tab ledger session management with TTL expiry and LRU cap."""
from __future__ import annotations

import threading
import time

from greg.config import load_settings
from greg.domain.ledger import LedgerStore


class SessionManager:
    """Thread-safe in-memory per-tab store manager with TTL and LRU-style cap."""

    def __init__(self, max_sessions: int | None = None, ttl_seconds: float | None = None) -> None:
        settings = load_settings()
        self.max_sessions = max(
            1, int(settings.session_max if max_sessions is None else max_sessions)
        )
        self.ttl_seconds = max(
            1.0, float(settings.session_ttl if ttl_seconds is None else ttl_seconds)
        )
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[LedgerStore, float]] = {}

    def get(self, session_id: str | None) -> LedgerStore:
        """Return the session's `LedgerStore`, creating or evicting sessions as needed."""
        if not session_id or not session_id.strip():
            return STORE
        sid = session_id.strip()[:64]
        with self._lock:
            now = time.monotonic()
            expired = [k for k, (_, ts) in self._sessions.items() if now - ts > self.ttl_seconds]
            for k in expired:
                del self._sessions[k]

            if sid in self._sessions:
                store, _ = self._sessions[sid]
                self._sessions[sid] = (store, now)
                return store

            while len(self._sessions) >= self.max_sessions:
                oldest_key = min(self._sessions, key=lambda k: self._sessions[k][1])
                del self._sessions[oldest_key]

            fresh = LedgerStore()
            self._sessions[sid] = (fresh, now)
            return fresh

    def clear_all(self) -> None:
        """Reset the fallback global store and remove all active tab sessions."""
        STORE.reset()
        with self._lock:
            self._sessions.clear()


STORE = LedgerStore()
SESSIONS = SessionManager()
