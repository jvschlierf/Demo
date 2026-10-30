from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from xml.sax.saxutils import escape

import pytest

from demo import sources
from demo.brands import load_brands
from demo.models import Brand, Sector
from demo.sources import (
    Feed,
    SourceError,
    collect,
    fetch,
    google_news_feed,
    parse_feed,
    reddit_feed,
)

FIXTURES = Path(__file__).parent / "fixtures"
GOOGLE = Feed("Google News: Burberry", "https://example.test/google", "news")
TRADE = Feed("Business of Fashion", "https://example.test/bof", "news")
REDDIT = Feed("Reddit", "https://example.test/reddit", "social")
SINCE = datetime(2026, 10, 29, tzinfo=UTC)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_google_news_uses_outlet_as_source() -> None:
    [item] = parse_feed(fixture("google_news.xml"), GOOGLE)
    assert item.source == "Reuters"
    assert item.kind == "news"
    assert item.title == "Burberry weighs sale of beauty licence, sources say"
    assert item.url == "https://news.google.com/rss/articles/abc123?oc=5"
    assert item.published == datetime(2026, 10, 30, 7, 15, tzinfo=UTC)
    assert item.text == "Burberry weighs sale Reuters"


def test_parse_rss_skips_items_missing_link_or_valid_date() -> None:
    titles = [item.title for item in parse_feed(fixture("google_news.xml"), GOOGLE)]
    assert not any("no date" in t or "bad date" in t or "no link" in t for t in titles)


def test_parse_rss_falls_back_to_feed_name_and_normalises_timezone() -> None:
    gucci = parse_feed(fixture("trade.xml"), TRADE)[0]
    assert gucci.source == "Business of Fashion"
    assert gucci.published == datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
    assert gucci.text == "The label’s new flagship offers a first look."


def test_parse_atom_reddit_entries() -> None:
    items = parse_feed(fixture("reddit.xml"), REDDIT)
    assert [i.source for i in items] == ["r/Sneakers", "r/fashion"]
    nike = items[0]
    assert nike.kind == "social"
    assert nike.url == "https://www.reddit.com/r/Sneakers/comments/1abcde/nike_pulling_dunks/"
    assert nike.published == datetime(2026, 10, 30, 8, 30, tzinfo=UTC)
    assert "Nike is pulling Dunks from JD & Size?" in nike.text
    assert items[1].published == datetime(2026, 10, 30, 8, 0, tzinfo=UTC)


def test_parse_empty_channel_returns_no_items() -> None:
    assert parse_feed(b'<rss version="2.0"><channel></channel></rss>', TRADE) == []


def test_parse_malformed_xml_raises() -> None:
    with pytest.raises(SourceError, match="malformed XML"):
        parse_feed(b"<rss><channel><item>", TRADE)


def test_parse_non_feed_document_raises() -> None:
    with pytest.raises(SourceError, match="unrecognised feed format <html>"):
        parse_feed(b"<html><body>Blocked</body></html>", TRADE)


def test_google_news_feed_queries_name_and_aliases_in_uk_edition() -> None:
    brand = Brand("Kering", Sector.LUXURY, "KER.PA", ("Gucci",))
    feed = google_news_feed(brand, days=2)
    assert feed.name == "Google News: Kering"
    assert "q=%22Kering%22+OR+%22Gucci%22+when%3A2d" in feed.url
    assert feed.url.endswith("&hl=en-GB&gl=GB&ceid=GB:en")


def test_reddit_feed_combines_subreddits() -> None:
    feed = reddit_feed(("Sneakers", "fashion"))
    assert feed.url == "https://www.reddit.com/r/Sneakers+fashion/new/.rss?limit=100"
    assert feed.kind == "social"


def test_fetch_wraps_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise URLError("connection refused")

    monkeypatch.setattr(sources, "urlopen", fail)
    with pytest.raises(SourceError, match="connection refused"):
        fetch("https://example.test/feed")


def test_collect_filters_by_date_and_brand_and_reports_unavailable() -> None:
    down = Feed("Drapers", "https://example.test/down", "news")
    responses = {
        GOOGLE.url: fixture("google_news.xml"),
        TRADE.url: fixture("trade.xml"),
        REDDIT.url: fixture("reddit.xml"),
    }

    def fetcher(url: str) -> bytes:
        if url not in responses:
            raise SourceError(f"{url}: HTTP Error 403")
        return responses[url]

    result = collect(load_brands(), SINCE, feeds=[GOOGLE, TRADE, down, REDDIT], fetcher=fetcher)

    titles = [item.title for item in result.items]
    assert titles == [
        "Burberry weighs sale of beauty licence, sources say",
        "Inside Gucci’s Paris Palazzo, a Blueprint for Its Retail Revamp",
        "Rumour: Nike pulling Dunks from JD?",
    ]
    assert result.unavailable == ["Drapers"]


def collect_one(title: str, source: str = "Reuters") -> list[str]:
    feed = (
        f'<rss version="2.0"><channel><item><title>{escape(title)}</title>'
        "<link>https://example.test/a</link>"
        "<pubDate>Fri, 30 Oct 2026 07:15:00 GMT</pubDate>"
        f"<source>{escape(source)}</source></item></channel></rss>"
    ).encode()
    result = collect(load_brands(), SINCE, feeds=[GOOGLE], fetcher=lambda url: feed)
    return [item.title for item in result.items]


@pytest.mark.parametrize("source", ["Alphaville e Arredores", "MarketBeat", "Seeking Alpha"])
def test_collect_drops_blocked_sources(source: str) -> None:
    assert collect_one("Burberry weighs beauty licence sale", source) == []


@pytest.mark.parametrize(
    "title",
    [
        "Burberry Group plc (BRBY.L) Stock Price, News, Quote & History",
        "Is Nike Stock A Real Bargain?",
        "Puma shares up 6.7% as Q3 sales beat forecasts",
        "Hermès share down 25.5% over three months",
        "JPMorgan raises Adidas price target to €230",
        "PUMA SE : Gets a Neutral rating from Jefferies",
        'Capri Holdings Stock Rated "Hold" by Wall Street Brokerages',
        "NIKE (NKE) Could Be 6% Undervalued After India Focus",
        "Kering Short Interest Report for October",
        "Shareholders who sold shares of Skechers should contact Wolf Haldenstein",
    ],
)
def test_collect_drops_market_data_titles(title: str) -> None:
    assert collect_one(title) == []


@pytest.mark.parametrize(
    "title",
    [
        "Adidas upgrades profit outlook as quarterly sales and profits jump",
        "Puma trims 2026 operating loss forecast amid softer consumer demand",
        "Nike strengthens position in India with first locally built shoe",
        "Ian Jackson shares advice on Cartier collecting",
        "LVMH takes stake in Italian cashmere supplier",
    ],
)
def test_collect_keeps_business_news(title: str) -> None:
    assert collect_one(title) == [title]


def test_collect_with_no_feeds_returns_empty() -> None:
    result = collect(load_brands(), SINCE, feeds=[], fetcher=lambda url: b"")
    assert result.items == []
    assert result.unavailable == []
