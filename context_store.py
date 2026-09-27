"""
Vera Bot — In-Memory Context Store
====================================
Stores category, merchant, customer, and trigger contexts pushed by the judge.
Idempotent by (scope, context_id, version). Higher version replaces atomically.
"""

from __future__ import annotations
import threading
from datetime import datetime, timezone
from typing import Any, Optional


class ContextStore:
    """Thread-safe in-memory store for all four context scopes."""

    def __init__(self):
        # (scope, context_id) -> {version: int, payload: dict, stored_at: str}
        self._data: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()
        # Track suppressed conversation_ids (merchant said stop)
        self.suppressed_conversations: set[str] = set()
        # Track suppressed merchants (hostile exit)
        self.suppressed_merchants: set[str] = set()
        # Track which triggers have already been actioned this session
        self.actioned_triggers: set[str] = set()

    # ── write ────────────────────────────────────────────────────────────
    def upsert(
        self, scope: str, context_id: str, version: int, payload: dict
    ) -> tuple[bool, Optional[int], str]:
        """
        Store or update a context.
        Returns (accepted, current_version_if_conflict, stored_at_iso).
        """
        key = (scope, context_id)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._lock:
            existing = self._data.get(key)
            if existing and existing["version"] >= version:
                return False, existing["version"], now_iso

            self._data[key] = {
                "version": version,
                "payload": payload,
                "stored_at": now_iso,
            }
            return True, None, now_iso

    # ── read ─────────────────────────────────────────────────────────────
    def get(self, scope: str, context_id: str) -> Optional[dict]:
        """Return the payload for a (scope, context_id), or None."""
        entry = self._data.get((scope, context_id))
        return entry["payload"] if entry else None

    def get_version(self, scope: str, context_id: str) -> Optional[int]:
        entry = self._data.get((scope, context_id))
        return entry["version"] if entry else None

    # ── queries ──────────────────────────────────────────────────────────
    def counts(self) -> dict[str, int]:
        counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        for (scope, _) in self._data:
            if scope in counts:
                counts[scope] += 1
        return counts

    def get_category(self, slug: str) -> Optional[dict]:
        return self.get("category", slug)

    def get_merchant(self, merchant_id: str) -> Optional[dict]:
        return self.get("merchant", merchant_id)

    def get_customer(self, customer_id: str) -> Optional[dict]:
        return self.get("customer", customer_id)

    def get_trigger(self, trigger_id: str) -> Optional[dict]:
        return self.get("trigger", trigger_id)

    def all_merchants(self) -> list[dict]:
        return [
            entry["payload"]
            for (scope, _), entry in self._data.items()
            if scope == "merchant"
        ]

    def all_triggers(self) -> list[dict]:
        return [
            entry["payload"]
            for (scope, _), entry in self._data.items()
            if scope == "trigger"
        ]
