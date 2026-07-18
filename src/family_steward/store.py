"""SQLite persistence for items, pending actions, and audit events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('grocery', 'task', 'note')),
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                correlation_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY,
                action_type TEXT NOT NULL CHECK (
                    action_type IN ('calendar.create', 'message.send')
                ),
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'pending_approval', 'approved', 'denied',
                        'succeeded', 'failed'
                    )
                ),
                correlation_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_result_category TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY,
                correlation_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def append_event(
        self, correlation_id: str, event_type: str, detail: dict[str, Any]
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_events (
                correlation_id, event_type, detail_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (correlation_id, event_type, json.dumps(detail, sort_keys=True), utc_now()),
        )
        self.connection.commit()

    def create_item(self, kind: str, title: str, correlation_id: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """
            INSERT INTO items (kind, title, correlation_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (kind, title, correlation_id, utc_now()),
        )
        self.connection.commit()
        return self.get_item(cursor.lastrowid)

    def get_item(self, item_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"item {item_id} not found")
        return dict(row)

    def list_items(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is None:
            rows = self.connection.execute("SELECT * FROM items ORDER BY id").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM items WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        return [dict(row) for row in rows]

    def find_action_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM actions WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return dict(row) if row else None

    def create_action(
        self,
        action_type: str,
        payload: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        existing = self.find_action_by_key(idempotency_key)
        if existing:
            return existing
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO actions (
                action_type, payload_json, status, correlation_id,
                idempotency_key, created_at, updated_at
            ) VALUES (?, ?, 'pending_approval', ?, ?, ?, ?)
            """,
            (
                action_type,
                json.dumps(payload, sort_keys=True),
                correlation_id,
                idempotency_key,
                now,
                now,
            ),
        )
        self.connection.commit()
        return self.get_action(cursor.lastrowid)

    def get_action(self, action_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"action {action_id} not found")
        action = dict(row)
        action["payload"] = json.loads(action.pop("payload_json"))
        return action

    def set_action_status(
        self,
        action_id: int,
        status: str,
        *,
        attempt_count: int | None = None,
        result_category: str | None = None,
    ) -> dict[str, Any]:
        action = self.get_action(action_id)
        next_attempt_count = (
            action["attempt_count"] if attempt_count is None else attempt_count
        )
        self.connection.execute(
            """
            UPDATE actions
            SET status = ?, attempt_count = ?, last_result_category = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, next_attempt_count, result_category, utc_now(), action_id),
        )
        self.connection.commit()
        return self.get_action(action_id)

    def list_events(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM audit_events ORDER BY id"
        ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["detail"] = json.loads(event.pop("detail_json"))
            events.append(event)
        return events
