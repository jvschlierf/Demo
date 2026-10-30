from datetime import UTC, date, datetime

from demo.models import Analysis, Brand, ItemKind, RawItem, Sector, Sentiment, Status, Story
from demo.report import Entry, Report, render_html, render_markdown

BRANDS = {
    "Burberry": Brand("Burberry", Sector.LUXURY, "BRBY.L"),
    "Moncler": Brand("Moncler", Sector.LUXURY, "MONC.MI"),
    "Nike": Brand("Nike", Sector.SPORTSWEAR, "NKE"),
}


def item(source: str, url: str, kind: ItemKind = "news") -> RawItem:
    return RawItem(source, kind, "t", url, datetime(2026, 10, 30, 7, tzinfo=UTC))


def story(sid: str, title: str, *items: RawItem) -> Story:
    return Story(sid, title, list(items) or [item("Reuters", f"https://a.test/{sid}")])


def entry(
    sid: str,
    brand: str,
    title: str,
    status: Status = Status.CONFIRMED,
    materiality: int = 3,
    **kw: object,
) -> Entry:
    analysis = Analysis(sid, brand, True, status, Sentiment.NEGATIVE, materiality, f"why {sid}")
    for key, value in kw.items():
        setattr(analysis, key, value)
    return Entry(analysis, story(sid, title))


def report(
    *entries: Entry, unavailable: list[str] | None = None, unanalysed: list[Story] | None = None
) -> Report:
    return Report(
        day=date(2026, 10, 30),
        generated_at=datetime(2026, 10, 30, 7, 5, tzinfo=UTC),
        window_hours=24,
        brands=BRANDS,
        summary=["Burberry: licence sale rumour."],
        entries=list(entries),
        unanalysed=unanalysed or [],
        unavailable=unavailable or [],
        stats={"Feed items": 10, "Web items": 2},
    )


def test_rumour_watch_comes_first_sorted_by_materiality() -> None:
    md = render_markdown(
        report(
            entry("a", "Nike", "Nike CEO exit rumoured", Status.RUMOUR, 3),
            entry("b", "Burberry", "Burberry licence sale rumoured", Status.RUMOUR, 5),
            entry("c", "Burberry", "Burberry opens store", Status.CONFIRMED, 4),
        )
    )
    rumour_watch = md[md.index("## Rumour watch") : md.index("## Luxury")]
    assert rumour_watch.index("Burberry licence sale") < rumour_watch.index("Nike CEO exit")
    assert "Burberry opens store" not in rumour_watch
    assert md.index("## Summary") < md.index("## Rumour watch")


def test_no_rumours_and_quiet_brands() -> None:
    md = render_markdown(report(entry("c", "Burberry", "Burberry opens store")))
    assert "_No rumours today._" in md
    assert "_No new coverage: Moncler_" in md
    assert "_No new coverage: Nike_" in md


def test_entry_shows_tags_id_and_inline_sources() -> None:
    e = entry("abc123", "Burberry", "Burberry opens store", Status.RUMOUR, 4)
    e.story.items = [
        item("Reuters", "https://reuters.test/1"),
        item("r/handbags", "https://reddit.test/2", "social"),
        item("ft.com", "https://ft.test/3", "web"),
        item("Reuters", "https://reuters.test/4"),  # same outlet again: listed once
        item("BoF", "https://bof.test/5"),
        item("WWD", "https://wwd.test/6"),
    ]
    md = render_markdown(report(e))
    assert "- ▼ **Burberry** · [Burberry opens store](https://reuters.test/1)" in md
    assert "negative · rumour · materiality 4 · `abc123`" in md
    assert (
        "Sources: [Reuters](https://reuters.test/1) (news) · [r/handbags](https://reddit.test/2)"
        " (social) · [ft.com](https://ft.test/3) (web) · +2 more"
    ) in md
    html = render_html(report(e))
    assert "<details class='more'><summary>+2 more</summary>" in html
    assert "<a href='https://wwd.test/6' rel='noopener'>WWD</a>" in html


def test_pm_override_and_status_change_are_marked() -> None:
    e = entry(
        "a",
        "Burberry",
        "Burberry licence talks confirmed",
        sentiment_source="pm",
        pm_note="licence is core income",
    )
    e.previous_status = Status.RUMOUR
    md = render_markdown(report(e))
    assert "negative (PM) · confirmed · materiality 3 · was rumour" in md
    assert "PM note: licence is core income" in md
    html = render_html(report(e))
    assert "class='entry negative pm'" in html


def test_minor_stories_listed_briefly() -> None:
    md = render_markdown(report(entry("m", "Nike", "Nike opens pop-up", materiality=1)))
    assert "- Minor: [Nike opens pop-up](https://a.test/m) · [Reuters]" in md
    assert "why m" not in md


def test_outside_region_is_tagged() -> None:
    md = render_markdown(report(entry("a", "Nike", "Nike India shoe", region_relevant=False)))
    assert "outside UK/EMEA" in md


def test_html_escapes_text_and_drops_unsafe_links() -> None:
    e = entry("x", "Nike", "<script>alert(1)</script> Nike")
    e.story.items = [item("Evil", "javascript:alert(1)")]
    html = render_html(report(e))
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; Nike" in html
    assert "javascript:" not in html
    md = render_markdown(report(e))
    assert "(javascript:" not in md


def test_footer_lists_unavailable_sources_only_when_present() -> None:
    assert "Sources unavailable" not in render_markdown(report())
    md = render_markdown(report(unavailable=["Reddit", "Web search: Nike"]))
    assert "Sources unavailable this run: Reddit, Web search: Nike" in md
    assert "Feed items: 10 · Web items: 2" in md


def test_unanalysed_stories_are_listed() -> None:
    md = render_markdown(report(unanalysed=[story("u", "Nike recall reported")]))
    assert "## Not analysed" in md
    assert "Nike recall reported" in md


def test_html_is_a_complete_page_with_dark_mode() -> None:
    html = render_html(report(entry("a", "Burberry", "Burberry opens store")))
    assert html.startswith("<!doctype html>")
    assert "<title>UK/EMEA Luxury &amp; Sportswear Brief · Friday 30 October 2026</title>" in html
    assert "prefers-color-scheme: dark" in html
    assert "<li>Burberry: licence sale rumour.</li>" in html
