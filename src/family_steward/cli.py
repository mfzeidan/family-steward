"""Command-line interface for the offline Family Steward reference app."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .service import FamilySteward
from .store import Store


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def run_scenario(app: FamilySteward, fixture_path: str) -> dict[str, Any]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    references: dict[str, int] = {}
    results = []
    for operation in fixture["operations"]:
        name = operation["operation"]
        if name == "add_item":
            result = app.add_item(operation["kind"], operation["title"])
        elif name == "prepare_action":
            result = app.prepare_action(operation["action_type"], operation["payload"])
        elif name == "approve":
            result = app.approve(references[operation["ref"]], operation["actor"])
        elif name == "deny":
            result = app.deny(references[operation["ref"]], operation["actor"])
        elif name == "retry":
            result = app.retry(references[operation["ref"]], operation["actor"])
        else:
            raise ValueError(f"unsupported fixture operation: {name}")
        if "save_as" in operation:
            references[operation["save_as"]] = int(result["id"])
        results.append({"operation": name, "status": result.get("status", "created")})
    return {
        "household": fixture["household"],
        "operations": results,
        "audit_event_count": len(app.store.list_events()),
    }


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="family-steward",
        description="Offline synthetic household operations reference application",
    )
    cli.add_argument(
        "--db",
        default=os.environ.get(
            "FAMILY_STEWARD_STATE_PATH", "./var/family-steward.db"
        ),
        help="SQLite state path",
    )
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize durable local state")

    add = commands.add_parser("add", help="Add a grocery, task, or note")
    add.add_argument("kind", choices=["grocery", "task", "note"])
    add.add_argument("title")

    list_items = commands.add_parser("list", help="List durable items")
    list_items.add_argument("--kind", choices=["grocery", "task", "note"])

    calendar = commands.add_parser(
        "prepare-calendar", help="Prepare a mock calendar action for approval"
    )
    calendar.add_argument("title")
    calendar.add_argument("when")
    calendar.add_argument("--failures-before-success", type=int, default=0)

    message = commands.add_parser(
        "prepare-message", help="Prepare a mock message action for approval"
    )
    message.add_argument("recipient")
    message.add_argument("body")
    message.add_argument("--failures-before-success", type=int, default=0)

    for name in ("approve", "deny", "retry"):
        decision = commands.add_parser(name)
        decision.add_argument("action_id", type=int)
        decision.add_argument("--actor", default="Casey")

    commands.add_parser("history", help="Show the append-only action history")
    scenario = commands.add_parser("scenario", help="Run a synthetic fixture")
    scenario.add_argument("fixture")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = Store(args.db)
    app = FamilySteward(store)
    try:
        if args.command == "init":
            emit({"status": "initialized", "state": str(Path(args.db))})
        elif args.command == "add":
            emit(app.add_item(args.kind, args.title))
        elif args.command == "list":
            emit(store.list_items(args.kind))
        elif args.command == "prepare-calendar":
            emit(
                app.prepare_action(
                    "calendar.create",
                    {
                        "title": args.title,
                        "when": args.when,
                        "failures_before_success": args.failures_before_success,
                    },
                )
            )
        elif args.command == "prepare-message":
            emit(
                app.prepare_action(
                    "message.send",
                    {
                        "recipient": args.recipient,
                        "body": args.body,
                        "failures_before_success": args.failures_before_success,
                    },
                )
            )
        elif args.command == "approve":
            emit(app.approve(args.action_id, args.actor))
        elif args.command == "deny":
            emit(app.deny(args.action_id, args.actor))
        elif args.command == "retry":
            emit(app.retry(args.action_id, args.actor))
        elif args.command == "history":
            emit(store.list_events())
        elif args.command == "scenario":
            emit(run_scenario(app, args.fixture))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
