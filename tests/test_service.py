import tempfile
import unittest
from pathlib import Path

from family_steward.service import FamilySteward
from family_steward.store import Store


class FamilyStewardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "state.db"
        self.store = Store(self.database_path)
        self.app = FamilySteward(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_creates_durable_items_without_approval(self) -> None:
        grocery = self.app.add_item("grocery", "Oat milk")
        task = self.app.add_item("task", "Pack the library books")
        note = self.app.add_item("note", "Use the blue reusable bag")

        self.assertEqual([grocery, task, note], self.store.list_items())
        self.assertEqual(
            ["item.created", "item.created", "item.created"],
            [event["event_type"] for event in self.store.list_events()],
        )

    def test_external_style_action_requires_approval(self) -> None:
        action = self.app.prepare_action(
            "calendar.create",
            {"title": "School pickup", "when": "Thursday at 3:15 PM"},
        )

        self.assertEqual("pending_approval", action["status"])
        self.assertEqual(0, action["attempt_count"])

        completed = self.app.approve(action["id"], "Casey")

        self.assertEqual("succeeded", completed["status"])
        self.assertEqual(1, completed["attempt_count"])
        self.assertEqual("mock_success", completed["last_result_category"])

    def test_denied_action_never_calls_mock_tool(self) -> None:
        action = self.app.prepare_action(
            "message.send",
            {"recipient": "Jordan", "body": "The books are packed."},
        )

        denied = self.app.deny(action["id"], "Casey")

        self.assertEqual("denied", denied["status"])
        self.assertEqual(0, denied["attempt_count"])
        event_types = [event["event_type"] for event in self.store.list_events()]
        self.assertNotIn("mock_tool.started", event_types)

    def test_failure_is_visible_and_retry_is_idempotent(self) -> None:
        payload = {
            "recipient": "Jordan",
            "body": "The books are packed.",
            "failures_before_success": 1,
        }
        action = self.app.prepare_action("message.send", payload)
        duplicate = self.app.prepare_action("message.send", payload)
        self.assertEqual(action["id"], duplicate["id"])

        failed = self.app.approve(action["id"], "Casey")
        self.assertEqual("failed", failed["status"])
        self.assertEqual("simulated_timeout", failed["last_result_category"])

        succeeded = self.app.retry(action["id"], "Casey")
        self.assertEqual("succeeded", succeeded["status"])
        self.assertEqual(2, succeeded["attempt_count"])

    def test_state_survives_restart(self) -> None:
        created = self.app.add_item("task", "Pack the library books")
        self.store.close()

        reopened_store = Store(self.database_path)
        reopened_app = FamilySteward(reopened_store)
        try:
            self.assertEqual(created["id"], reopened_store.list_items()[0]["id"])
            self.assertEqual(1, len(reopened_app.store.list_events()))
        finally:
            reopened_store.close()


if __name__ == "__main__":
    unittest.main()
