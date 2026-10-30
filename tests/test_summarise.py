from datetime import UTC, datetime
from typing import cast

import anthropic

from demo.models import Analysis, RawItem, Sentiment, Status, Story
from demo.summarise import summarise
from tests.fake_claude import FakeClient, refusal, reply

ITEM = RawItem("Reuters", "news", "t", "https://a.test", datetime(2026, 10, 30, tzinfo=UTC))
STORIES = {
    "a": Story("a", "Burberry weighs licence sale", [ITEM]),
    "b": Story("b", "Nike opens Manchester store", [ITEM]),
}


def analysis(sid: str, brand: str, materiality: int, **kw: object) -> Analysis:
    fields = dict(
        story_id=sid,
        brand=brand,
        region_relevant=True,
        status=Status.RUMOUR,
        sentiment=Sentiment.POSITIVE,
        materiality=materiality,
        why="why",
    )
    return Analysis(**(fields | kw))  # type: ignore[arg-type]


def test_summarise_sends_notable_stories_most_material_first() -> None:
    fake = FakeClient(reply({"bullets": ["Burberry: licence sale rumour."]}))
    analyses = [
        analysis("b", "Nike", 3),
        analysis(
            "a",
            "Burberry",
            5,
            sentiment=Sentiment.NEGATIVE,
            sentiment_source="pm",
            pm_note="core income",
        ),
    ]
    bullets = summarise(analyses, STORIES, cast(anthropic.Anthropic, fake))
    assert bullets == ["Burberry: licence sale rumour."]
    lines = fake.prompt().splitlines()
    assert lines[0].startswith("- Burberry | rumour | negative (PM view) | materiality 5")
    assert lines[0].endswith("(PM note: core income)")
    assert lines[1].startswith("- Nike | rumour | positive (model view) | materiality 3")
    assert fake.calls[0]["output_config"]["effort"] == "medium"


def test_summarise_skips_call_when_nothing_material() -> None:
    fake = FakeClient()
    assert summarise([analysis("b", "Nike", 2)], STORIES, cast(anthropic.Anthropic, fake)) == []
    assert summarise([], STORIES, cast(anthropic.Anthropic, fake)) == []
    assert fake.calls == []


def test_summarise_returns_empty_when_claude_fails() -> None:
    fake = FakeClient(refusal(), refusal())
    assert summarise([analysis("a", "Burberry", 4)], STORIES, cast(anthropic.Anthropic, fake)) == []


def test_summarise_caps_bullets() -> None:
    fake = FakeClient(reply({"bullets": [f"b{n}" for n in range(9)]}))
    bullets = summarise([analysis("a", "Burberry", 4)], STORIES, cast(anthropic.Anthropic, fake))
    assert bullets == [f"b{n}" for n in range(6)]
