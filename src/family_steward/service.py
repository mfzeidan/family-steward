"""Application service with deterministic policy and approval boundaries."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .adapters import MockAdapter, default_adapters
from .store import Store


ALLOWED_ITEM_KINDS = {"grocery", "task", "note"}
APPROVAL_ACTIONS = {"calendar.create", "message.send"}


class FamilySteward:
    def __init__(
        self, store: Store, adapters: dict[str, MockAdapter] | None = None
    ) -> None:
        self.store = store
        self.adapters = adapters or default_adapters()
        self.store.initialize()

    @staticmethod
    def _correlation_id() -> str:
        return f"corr-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _idempotency_key(action_type: str, payload: dict[str, Any]) -> str:
        material = json.dumps(
            {"action_type": action_type, "payload": payload}, sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def add_item(self, kind: str, title: str) -> dict[str, Any]:
        clean_kind = kind.strip().lower()
        clean_title = title.strip()
        if clean_kind not in ALLOWED_ITEM_KINDS:
            raise ValueError("kind must be grocery, task, or note")
        if not clean_title:
            raise ValueError("title is required")
        correlation_id = self._correlation_id()
        item = self.store.create_item(clean_kind, clean_title, correlation_id)
        self.store.append_event(
            correlation_id,
            "item.created",
            {"item_id": item["id"], "kind": clean_kind},
        )
        return item

    def prepare_action(
        self,
        action_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if action_type not in APPROVAL_ACTIONS:
            raise ValueError("unsupported action type")
        correlation_id = self._correlation_id()
        key = idempotency_key or self._idempotency_key(action_type, payload)
        existing = self.store.find_action_by_key(key)
        action = self.store.create_action(action_type, payload, correlation_id, key)
        if existing is None:
            self.store.append_event(
                correlation_id,
                "approval.requested",
                {"action_id": action["id"], "action_type": action_type},
            )
        return self.store.get_action(action["id"])

    def approve(self, action_id: int, actor: str) -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action["status"] != "pending_approval":
            raise ValueError("only pending actions can be approved")
        approved = self.store.set_action_status(action_id, "approved")
        self.store.append_event(
            approved["correlation_id"],
            "approval.granted",
            {"action_id": action_id, "actor": actor},
        )
        return self._execute(action_id)

    def deny(self, action_id: int, actor: str) -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action["status"] != "pending_approval":
            raise ValueError("only pending actions can be denied")
        denied = self.store.set_action_status(action_id, "denied")
        self.store.append_event(
            denied["correlation_id"],
            "approval.denied",
            {"action_id": action_id, "actor": actor},
        )
        return denied

    def retry(self, action_id: int, actor: str) -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action["status"] != "failed":
            raise ValueError("only failed actions can be retried")
        approved = self.store.set_action_status(action_id, "approved")
        self.store.append_event(
            approved["correlation_id"],
            "action.retry_requested",
            {"action_id": action_id, "actor": actor},
        )
        return self._execute(action_id)

    def _execute(self, action_id: int) -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action["status"] != "approved":
            raise ValueError("action must be approved before execution")
        adapter = self.adapters[action["action_type"]]
        attempt = action["attempt_count"] + 1
        self.store.append_event(
            action["correlation_id"],
            "mock_tool.started",
            {"action_id": action_id, "adapter": adapter.name, "attempt": attempt},
        )
        result = adapter.execute(action["payload"], attempt)
        status = "succeeded" if result.success else "failed"
        updated = self.store.set_action_status(
            action_id,
            status,
            attempt_count=attempt,
            result_category=result.category,
        )
        self.store.append_event(
            updated["correlation_id"],
            f"mock_tool.{status}",
            {
                "action_id": action_id,
                "adapter": adapter.name,
                "attempt": attempt,
                "result_category": result.category,
            },
        )
        return updated
