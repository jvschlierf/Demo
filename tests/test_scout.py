import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2 as httpx
import pytest

from demo.models import Brand, Sector
from demo.scout import SEARCH_TOOL, parse_page_age, scout_all, scout_brand
from tests.fake_claude import FakeClient

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
SINCE = NOW - timedelta(hours=24)
BURBERRY = Brand("Burberry", Sector.LUXURY, "BRBY.L", ("Burberry Beauty",), scout=True)
BRANDS = {
    "Burberry": BURBERRY,
    "Nike": Brand("Nike", Sector.SPORTSWEAR, "NKE", scout=False),
}


def result(url: str, title: str, page_age: str | None) -> SimpleNamespace:
    return SimpleNamespace(type="web_search_result", url=url, title=title, page_age=page_age)


def search_block(*results: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(type="web_search_tool_result", content=list(results))


def response(
    *blocks: SimpleNamespace,
    findings: list[dict[str, Any]] | None = None,
    stop_reason: str = "end_turn",
) -> SimpleNamespace:
    content = list(blocks)
    if findings is not None:
        content.append(SimpleNamespace(type="text", text=json.dumps({"findings": findings})))
    return SimpleNamespace(stop_reason=stop_reason, content=content)


def finding(url: str, unconfirmed: bool = True) -> dict[str, Any]:
    return {"url": url, "summary": "Burberry is said to be in talks.", "unconfirmed": unconfirmed}


LICENCE = result(
    "https://www.ft.com/licence", "Burberry in talks over beauty licence", "0 days ago"
)
OLD = result("https://ft.com/old", "Burberry hires new designer", "228 days ago")
UNDATED = result("https://burberry.com/news", "Burberry Stories", None)


def scout(fake: FakeClient) -> list[Any] | None:
    return scout_brand(BURBERRY, SINCE, cast(anthropic.Anthropic, fake), now=NOW)


def test_scout_turns_findings_into_web_items() -> None:
    fake = FakeClient(response(search_block(LICENCE), findings=[finding(LICENCE.url)]))
    [item] = scout(fake)
    assert item.kind == "web"
    assert item.source == "ft.com"
    assert item.title == "Burberry in talks over beauty licence"  # title from the search result
    assert item.url == LICENCE.url
    assert item.published == NOW
    assert item.text == "Unconfirmed report: Burberry is said to be in talks."


def test_scout_sends_search_tool_and_prompt() -> None:
    fake = FakeClient(response(findings=[]))
    assert scout(fake) == []
    [call] = fake.calls
    assert call["tools"] == [SEARCH_TOOL]
    assert call["output_config"]["effort"] == "low"
    assert fake.prompt().startswith("Company: Burberry (brands and people: Burberry Beauty)")
    assert "last 24 hours" in fake.prompt()


def test_scout_ignores_urls_not_in_search_results() -> None:
    fake = FakeClient(response(search_block(LICENCE), findings=[finding("https://made.up/story")]))
    assert scout(fake) == []


def test_scout_drops_old_and_undated_results() -> None:
    fake = FakeClient(
        response(search_block(OLD, UNDATED), findings=[finding(OLD.url), finding(UNDATED.url)])
    )
    assert scout(fake) == []


def test_scout_skips_search_error_blocks() -> None:
    error = SimpleNamespace(
        type="web_search_tool_result", content=SimpleNamespace(error_code="max_uses_exceeded")
    )
    fake = FakeClient(response(error, search_block(LICENCE), findings=[finding(LICENCE.url)]))
    assert [i.url for i in scout(fake)] == [LICENCE.url]


def test_scout_resumes_paused_turn_with_transcript() -> None:
    paused = response(search_block(LICENCE), stop_reason="pause_turn")
    fake = FakeClient(paused, response(findings=[finding(LICENCE.url, unconfirmed=False)]))
    [item] = scout(fake)
    assert item.text == "Burberry is said to be in talks."  # results from the paused turn count
    resumed = fake.calls[1]["messages"]
    assert [m["role"] for m in resumed] == ["user", "assistant"]
    assert resumed[1]["content"] == paused.content


def test_scout_returns_none_without_usable_answer() -> None:
    assert scout(FakeClient(response(stop_reason="refusal"))) is None
    assert scout(FakeClient(response(SimpleNamespace(type="text", text="not json")))) is None


@pytest.mark.parametrize(
    ("page_age", "expected"),
    [
        ("0 days ago", NOW),
        ("3 hours ago", NOW - timedelta(hours=3)),
        ("1 day ago", NOW - timedelta(days=1)),
        ("2 weeks ago", NOW - timedelta(weeks=2)),
        ("April 30, 2026", datetime(2026, 4, 30, tzinfo=UTC)),
        ("Apr 30, 2026", datetime(2026, 4, 30, tzinfo=UTC)),
        ("2026-04-30", datetime(2026, 4, 30, tzinfo=UTC)),
        (None, None),
        ("", None),
        ("recently", None),
    ],
)
def test_parse_page_age(page_age: str | None, expected: datetime | None) -> None:
    assert parse_page_age(page_age, NOW) == expected


def test_scout_all_only_scouts_flagged_brands_and_filters_noise() -> None:
    quote = result("https://ft.com/q", "Burberry share price target raised", "0 days ago")
    fake = FakeClient(
        response(search_block(LICENCE, quote), findings=[finding(LICENCE.url), finding(quote.url)])
    )
    collected = scout_all(BRANDS, SINCE, cast(anthropic.Anthropic, fake))
    assert [i.url for i in collected.items] == [LICENCE.url]
    assert collected.unavailable == []
    assert len(fake.calls) == 1  # Nike has scout=False


def test_scout_all_reports_failed_brands() -> None:
    def fail(**kwargs: Any) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.APIConnectionError(request=request)

    fake = FakeClient()
    fake.beta.messages.create = fail
    collected = scout_all(BRANDS, SINCE, cast(anthropic.Anthropic, fake))
    assert collected.items == []
    assert collected.unavailable == ["Web search: Burberry"]
