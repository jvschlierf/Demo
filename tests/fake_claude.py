import json
from types import SimpleNamespace
from typing import Any


def reply(data: Any) -> SimpleNamespace:
    """A successful response: an (empty) thinking block followed by the JSON answer."""
    text = data if isinstance(data, str) else json.dumps(data)
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
    )


def refusal() -> SimpleNamespace:
    return SimpleNamespace(stop_reason="refusal", content=[])


class FakeClient:
    """Stands in for anthropic.Anthropic; returns canned replies in order and records calls."""

    def __init__(self, *replies: SimpleNamespace) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self.replies.pop(0)

    def prompt(self, call: int = -1) -> str:
        return self.calls[call]["messages"][0]["content"]
