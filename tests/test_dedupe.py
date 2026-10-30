from datetime import UTC, datetime

from demo.dedupe import canonical_url, cluster, merge_stories, story_id
from demo.models import Brand, ItemKind, RawItem, Sector, Story

BRANDS = {
    name: Brand(name, sector, None)
    for name, sector in [
        ("Burberry", Sector.LUXURY),
        ("Kering", Sector.LUXURY),
        ("LVMH", Sector.LUXURY),
        ("Prada", Sector.LUXURY),
        ("Nike", Sector.SPORTSWEAR),
    ]
}


def item(
    title: str,
    url: str,
    hour: int = 8,
    source: str = "Reuters",
    kind: ItemKind = "news",
) -> RawItem:
    return RawItem(source, kind, title, url, datetime(2026, 10, 30, hour, tzinfo=UTC))


def test_cluster_empty_input() -> None:
    assert cluster([], BRANDS) == []


def test_cluster_merges_reworded_headlines_from_two_outlets() -> None:
    reuters = item("Burberry weighs sale of beauty licence, sources say", "https://a.test/1", 7)
    ft = item("Burberry weighs beauty licence sale", "https://b.test/2", 9, "FT")
    [story] = cluster([ft, reuters], BRANDS)
    assert story.items == [reuters, ft]
    assert story.title == reuters.title
    assert story.id == story_id(reuters)


def test_cluster_keeps_different_stories_about_same_brand_apart() -> None:
    stories = cluster(
        [
            item("Nike cuts full-year revenue guidance", "https://a.test/1"),
            item("Nike names new chief marketing officer", "https://a.test/2"),
        ],
        BRANDS,
    )
    assert len(stories) == 2


def test_cluster_keeps_similar_headlines_about_different_brands_apart() -> None:
    stories = cluster(
        [
            item("LVMH Q3 earnings snapshot: revenue beats estimates", "https://a.test/1"),
            item("Prada Q3 earnings snapshot: revenue beats estimates", "https://a.test/2"),
        ],
        BRANDS,
    )
    assert len(stories) == 2


def test_cluster_needs_three_shared_words() -> None:
    stories = cluster(
        [
            item("Nike shares rise", "https://a.test/1"),
            item("Nike shares fall", "https://a.test/2"),
        ],
        BRANDS,
    )
    assert len(stories) == 2


def test_cluster_drops_same_article_from_another_feed() -> None:
    first = item("Kering and LVMH in talks", "https://www.a.test/story/?utm=x", 7)
    again = item("Kering and LVMH in talks", "https://a.test/story", 8, "Google News: LVMH")
    [story] = cluster([first, again], BRANDS)
    assert story.items == [first]


def test_cluster_prefers_news_headline_over_social_title() -> None:
    title = "Rumour: Nike pulling Dunks from JD stores"
    post = item(title, "https://reddit.test/1", 6, "r/Sneakers", "social")
    news = item("Nike pulling Dunks from JD stores, report says", "https://a.test/1", 9)
    [story] = cluster([news, post], BRANDS)
    assert story.title == news.title
    assert story.id == story_id(post)  # id still comes from the earliest item


def test_story_id_ignores_case_punctuation_url_and_time() -> None:
    a = item("Burberry: CEO to step down!", "https://a.test/1", 7)
    b = item("burberry CEO to step down", "https://b.test/2", 12)
    assert story_id(a) == story_id(b)
    assert story_id(a) != story_id(item("Burberry CEO stays on", "https://a.test/1"))


def test_canonical_url_strips_www_query_fragment_and_trailing_slash() -> None:
    assert canonical_url("https://WWW.Example.com/a/b/?x=1#top") == "example.com/a/b"


def test_merge_stories_combines_items_in_time_order_without_repeats() -> None:
    late = Story(
        "x", "Adidas sees Europe growth", [item("Adidas sees Europe growth", "https://b.test/2", 9)]
    )
    early_items = [
        item("Adidas raises outlook", "https://a.test/1", 6),
        item("Adidas sees Europe growth", "https://www.b.test/2/", 9, "Google News: Adidas"),
    ]
    early = Story("y", "Adidas raises outlook", early_items)
    merged = merge_stories([late, early])
    assert [i.url for i in merged.items] == ["https://a.test/1", "https://b.test/2"]
    assert merged.title == "Adidas raises outlook"
    assert merged.id == story_id(early_items[0])
