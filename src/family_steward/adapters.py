"""Deterministic mock adapters with no external connectivity."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    success: bool
    category: str


class MockAdapter:
    """Return a deterministic result based on the requested failure count."""

    def __init__(self, name: str) -> None:
        self.name = name

    def execute(self, payload: dict[str, Any], attempt: int) -> ToolResult:
        failures_before_success = int(payload.get("failures_before_success", 0))
        if attempt <= failures_before_success:
            return ToolResult(False, "simulated_timeout")
        return ToolResult(True, "mock_success")


def default_adapters() -> dict[str, MockAdapter]:
    return {
        "calendar.create": MockAdapter("mock_calendar"),
        "message.send": MockAdapter("mock_messaging"),
    }
