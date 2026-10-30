from datetime import UTC, datetime
from typing import Any, cast

import anthropic

from demo.analyse import analyse_stories, analyse_story, screen
from demo.models import Analysis, Brand, RawItem, Sector, Sentiment, Status, Story
from tests.fake_claude import FakeClient, refusal, reply

BRANDS = {
    "Nike": Brand("Nike", Sector.SPORTSWEAR, "NKE"),
    "Adidas": Brand("Adidas", Sector.SPORTSWEAR, "ADS.DE"),
    "Burberry": Brand("Burberry", Sector.LUXURY, "BRBY.L"),
}


def story(sid: str, title: str, text: str = "", sources: tuple[str, ...] = ("Reuters",)) -> Story:
    items = [
        RawItem(
            source,
            "news",
            title,
            f"https://a.test/{sid}/{n}",
            datetime(2026, 10, 30, 7, tzinfo=UTC),
            text,
        )
        for n, source in enumerate(sources)
    ]
    return Story(sid, title, items)


def entry(brand: str, **overrides: Any) -> dict[str, Any]:
    return {
        "brand": brand,
        "region_relevant": True,
        "status": "rumour",
        "sentiment": "negative",
        "materiality": 4,
        "why": "Licence income is a key profit driver.",
    } | overrides


def as_client(fake: FakeClient) -> anthropic.Anthropic:
    return cast(anthropic.Anthropic, fake)


def test_screen_keeps_selected_stories() -> None:
    stories = [story("b", "Nike shoe deal 50% off"), story("a", "Burberry weighs licence sale")]
    fake = FakeClient(reply({"groups": [[0]]}))
    assert screen(stories, BRANDS, as_client(fake)) == [stories[1]]
    prompt = fake.prompt()
    # Batched by brand, so Burberry sorts before Nike.
    assert "[0] Burberry weighs licence sale (Burberry; Reuters)" in prompt
    assert "[1] Nike shoe deal 50% off (Nike; Reuters)" in prompt
    assert fake.calls[0]["output_config"]["effort"] == "low"


def test_screen_merges_grouped_duplicates() -> None:
    early = Story(
        "early",
        "Adidas raises outlook",
        [
            RawItem(
                "Reuters",
                "news",
                "Adidas raises outlook",
                "https://a.test/1",
                datetime(2026, 10, 30, 6, tzinfo=UTC),
            )
        ],
    )
    late = Story(
        "late",
        "Adidas sees Europe growth in Q4",
        [
            RawItem(
                "FT",
                "news",
                "Adidas sees Europe growth in Q4",
                "https://b.test/2",
                datetime(2026, 10, 30, 9, tzinfo=UTC),
            )
        ],
    )
    fake = FakeClient(reply({"groups": [[1, 0]]}))  # indices follow title order: late, early
    [merged] = screen([early, late], BRANDS, as_client(fake))
    assert merged.title == "Adidas raises outlook"
    assert [i.source for i in merged.items] == ["Reuters", "FT"]


def test_screen_ignores_invalid_and_reused_indices() -> None:
    stories = [story("a", "Burberry weighs licence sale"), story("b", "Nike CEO steps down")]
    fake = FakeClient(reply({"groups": [[0, 5, -1], [0], [], [1, 1]]}))
    assert screen(stories, BRANDS, as_client(fake)) == stories


def test_screen_batches_headlines() -> None:
    stories = [story(str(n), f"Nike story {n}") for n in range(3)]
    fake = FakeClient(reply({"groups": [[1]]}), reply({"groups": [[0]]}))
    kept = screen(stories, BRANDS, as_client(fake), batch_size=2, max_workers=1)
    assert kept == [stories[1], stories[2]]
    assert len(fake.calls) == 2


def test_screen_keeps_whole_batch_when_claude_fails() -> None:
    stories = [story("a", "Burberry weighs licence sale")]
    assert screen(stories, BRANDS, as_client(FakeClient(refusal(), refusal()))) == stories


def test_screen_empty_input_makes_no_calls() -> None:
    fake = FakeClient()
    assert screen([], BRANDS, as_client(fake)) == []
    assert fake.calls == []


def test_analyse_story_parses_analysis() -> None:
    s = story("abc", "Burberry weighs licence sale, sources say", "Talks are early.")
    [analysis] = analyse_story(
        s, BRANDS, as_client(FakeClient(reply({"analyses": [entry("Burberry")]})))
    )
    assert analysis == Analysis(
        "abc",
        "Burberry",
        True,
        Status.RUMOUR,
        Sentiment.NEGATIVE,
        4,
        "Licence income is a key profit driver.",
    )


def test_analyse_story_one_analysis_per_brand() -> None:
    s = story("abc", "Adidas wins team kit deal from Nike")
    fake = FakeClient(reply({"analyses": [entry("Adidas", sentiment="positive"), entry("Nike")]}))
    analyses = analyse_story(s, BRANDS, as_client(fake))
    assert analyses is not None
    assert [(a.brand, a.sentiment) for a in analyses] == [
        ("Adidas", Sentiment.POSITIVE),
        ("Nike", Sentiment.NEGATIVE),
    ]


def test_analyse_story_prompt_and_schema() -> None:
    s = story("abc", "Burberry weighs licence sale", "x" * 2000, sources=("Reuters", "FT"))
    fake = FakeClient(reply({"analyses": []}))
    analyse_story(s, BRANDS, as_client(fake))
    prompt = fake.prompt()
    assert "Companies mentioned by keyword: Burberry" in prompt
    assert "Reported by 2 source(s):" in prompt
    assert "- FT (news, 2026-10-30 07:00 UTC): Burberry weighs licence sale" in prompt
    assert "x" * 601 not in prompt  # item text is truncated
    schema = fake.calls[0]["output_config"]["format"]["schema"]
    assert schema["properties"]["analyses"]["items"]["properties"]["brand"]["enum"] == list(BRANDS)


def test_analyse_story_namesake_returns_no_analyses() -> None:
    s = story("abc", "Nike the goddess in new museum show")
    assert analyse_story(s, BRANDS, as_client(FakeClient(reply({"analyses": []})))) == []


def test_analyse_story_rejects_unknown_sentiment() -> None:
    s = story("abc", "Burberry weighs licence sale")
    fake = FakeClient(reply({"analyses": [entry("Burberry", sentiment="bearish")]}))
    assert analyse_story(s, BRANDS, as_client(fake)) is None


def test_analyse_story_rejects_unknown_brand_and_bad_materiality() -> None:
    s = story("abc", "Burberry weighs licence sale")
    for bad in (entry("Gap"), entry("Burberry", materiality=9)):
        assert analyse_story(s, BRANDS, as_client(FakeClient(reply({"analyses": [bad]})))) is None


def test_analyse_stories_collects_unanalysed() -> None:
    ok, failed = story("a", "Burberry weighs licence sale"), story("b", "Nike CEO steps down")
    fake = FakeClient(reply({"analyses": [entry("Burberry")]}), refusal(), refusal())
    result = analyse_stories([ok, failed], BRANDS, as_client(fake), max_workers=1)
    assert [a.story_id for a in result.analyses] == ["a"]
    assert result.unanalysed == [failed]
