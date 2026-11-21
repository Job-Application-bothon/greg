"""Unit tests for `SessionManager` isolation, LRU eviction, and TTL expiry."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from greg.domain.sessions import SessionManager


class SessionManagerTest(unittest.TestCase):
    def test_session_manager_isolation_and_eviction(self) -> None:
        mgr = SessionManager(max_sessions=2, ttl_seconds=60)
        s_a = mgr.get("tab-a")
        s_b = mgr.get("tab-b")
        self.assertIsNot(s_a, s_b)
        s_a.add_manual_operation("2026-01-10", "income", 500.0, "Client A", "Other")
        self.assertTrue(s_a.get_view()["has_data"])
        self.assertFalse(s_b.get_view()["has_data"])

        # Adding a third session evicts the oldest accessed session (tab-a).
        _ = mgr.get("tab-c")
        s_a_new = mgr.get("tab-a")
        self.assertFalse(s_a_new.get_view()["has_data"])

        # TTL expiry creates a fresh store.
        with patch("greg.domain.sessions.time.monotonic", side_effect=[1000.0, 1000.0, 2000.0]):
            ttl_mgr = SessionManager(max_sessions=10, ttl_seconds=30)
            s1 = ttl_mgr.get("tab-ttl")
            s1.add_manual_operation("2026-01-10", "income", 100.0, "Client", "Other")
            self.assertTrue(ttl_mgr.get("tab-ttl").get_view()["has_data"])
            s1_expired = ttl_mgr.get("tab-ttl")
            self.assertFalse(s1_expired.get_view()["has_data"])


if __name__ == "__main__":
    unittest.main()
