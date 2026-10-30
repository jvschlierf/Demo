from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import anthropic
import pytest

from demo.models import Brand, Sector, Sentiment, Status
from demo.pipeline import build_report
from demo.store import Store
from tests.fake_claude import FakeClient, reply

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
BRANDS = {
    "Burberry": Brand("Burberry", Sector.LUXURY, "BRBY.L"),
    "Nike": Brand("Nike", Sector.SPORTSWEAR, "NKE"),
}
EMPTY_FEED = b'<rss version="2.0"><channel></channel></rss>'


def fetcher(url: str) -> bytes:
    if "Burberry" in url:
        return (FIXTURES / "google_news.xml").read_bytes()
    return EMPTY_FEED


def burberry_run(store: Store, now: datetime, window_hours: int = 24) -> tuple[object, FakeClient]:
    analysis = {
        "brand": "Burberry",
        "region_relevant": True,
        "status": "rumour",
        "sentiment": "neutral",
        "materiality": 4,
        "why": "Beauty licence income matters.",
    }
    fake = FakeClient(
        reply({"groups": [[0]]}),
        reply({"analyses": [analysis]}),
        reply({"bullets": ["Burberry may sell its beauty licence."]}),
    )
    report = build_report(
        BRANDS,
        store,
        cast(anthropic.Anthropic, fake),
        now=now,
        window=timedelta(hours=window_hours),
        web=False,
        fetcher=fetcher,
    )
    return report, fake


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "tracker.db")


def test_build_report_end_to_end(store: Store) -> None:
    report, fake = burberry_run(store, NOW)
    [entry] = report.entries
    assert entry.story.title == "Burberry weighs sale of beauty licence, sources say"
    assert (entry.analysis.status, entry.analysis.sentiment) == (Status.RUMOUR, Sentiment.NEUTRAL)
    assert entry.previous_status is None
    assert report.summary == ["Burberry may sell its beauty licence."]
    assert report.stats == {
        "Feed items": 1,
        "Web items": 0,
        "Stories": 1,
        "Kept after screening": 1,
    }
    assert report.unavailable == []
    assert len(fake.calls) == 3  # screen, analyse, summary; no web search
    assert store.brands_for(entry.story.id) == ["Burberry"]


def test_pm_override_carries_into_next_days_report(store: Store) -> None:
    first, _ = burberry_run(store, NOW)
    story_id = first.entries[0].story.id
    store.set_override(story_id, Sentiment.NEGATIVE, note="licence is core")

    second, fake = burberry_run(store, NOW + timedelta(days=1), window_hours=48)

    [entry] = second.entries
    assert entry.story.id == story_id
    assert entry.analysis.sentiment is Sentiment.NEGATIVE
    assert entry.analysis.sentiment_source == "pm"
    assert entry.previous_status is Status.RUMOUR
    assert "negative (PM view)" in fake.prompt()  # the summary sees the PM's view


def test_build_report_with_no_news_makes_no_claude_calls(store: Store) -> None:
    fake = FakeClient()
    report = build_report(
        BRANDS,
        store,
        cast(anthropic.Anthropic, fake),
        now=NOW,
        window=timedelta(hours=24),
        web=False,
        fetcher=lambda url: EMPTY_FEED,
    )
    assert report.entries == [] and report.summary == []
    assert fake.calls == []
