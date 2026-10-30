from types import SimpleNamespace
from typing import Any, cast

import anthropic

from demo.claude import FALLBACK_BETA, MODEL, structured_call
from tests.fake_claude import FakeClient, refusal, reply

SCHEMA = {"type": "object"}


def call(client: FakeClient) -> dict[str, Any] | None:
    return structured_call(
        cast(anthropic.Anthropic, client),
        system="sys",
        prompt="hello",
        schema=SCHEMA,
        effort="low",
    )


def test_returns_parsed_json_and_sends_expected_request() -> None:
    client = FakeClient(reply({"ok": True}))
    assert call(client) == {"ok": True}
    [request] = client.calls
    assert request["model"] == MODEL == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert request["betas"] == [FALLBACK_BETA]
    assert request["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert client.prompt() == "hello"


def test_uses_last_text_block_after_fallback() -> None:
    response = reply({"from": "fallback"})
    response.content.insert(0, SimpleNamespace(type="text", text='{"partial'))
    assert call(FakeClient(response)) == {"from": "fallback"}


def test_retries_once_after_refusal() -> None:
    client = FakeClient(refusal(), reply({"ok": True}))
    assert call(client) == {"ok": True}
    assert len(client.calls) == 2


def test_retries_once_after_invalid_json() -> None:
    client = FakeClient(reply("not json"), reply({"ok": True}))
    assert call(client) == {"ok": True}


def test_gives_up_after_two_failures() -> None:
    client = FakeClient(refusal(), SimpleNamespace(stop_reason="max_tokens", content=[]))
    assert call(client) is None
    assert len(client.calls) == 2
