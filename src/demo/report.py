import html
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit

from demo.models import Analysis, Brand, RawItem, Sector, Sentiment, Status, Story

MAX_INLINE_SOURCES = 3
# Stories at or above this materiality get a full entry; the rest are listed briefly.
MAJOR = 3

SENTIMENT_ICON = {Sentiment.POSITIVE: "▲", Sentiment.NEUTRAL: "●", Sentiment.NEGATIVE: "▼"}
SECTOR_TITLE = {Sector.LUXURY: "Luxury", Sector.SPORTSWEAR: "Sportswear"}


@dataclass
class Entry:
    analysis: Analysis
    story: Story
    previous_status: Status | None = None

    @property
    def status_changed(self) -> bool:
        return self.previous_status not in (None, self.analysis.status)


@dataclass
class Report:
    day: date
    generated_at: datetime
    window_hours: int
    brands: dict[str, Brand]
    summary: list[str]
    entries: list[Entry]
    unanalysed: list[Story]
    unavailable: list[str]
    stats: dict[str, int]  # label -> count, shown in the footer

    def rumours(self) -> list[Entry]:
        rumours = [e for e in self.entries if e.analysis.status is Status.RUMOUR]
        return sorted(rumours, key=lambda e: (-e.analysis.materiality, e.analysis.brand))

    def for_brand(self, brand: str) -> list[Entry]:
        entries = [e for e in self.entries if e.analysis.brand == brand]
        return sorted(entries, key=lambda e: -e.analysis.materiality)


def title(report: Report) -> str:
    return f"UK/EMEA Luxury & Sportswear Brief · {report.day:%A %d %B %Y}"


def sources(story: Story) -> list[RawItem]:
    """One item per outlet, in the order they reported the story."""
    seen: set[str] = set()
    unique = []
    for item in story.items:
        if item.source not in seen:
            seen.add(item.source)
            unique.append(item)
    return unique


def _safe_url(url: str) -> str | None:
    return url if urlsplit(url).scheme in ("http", "https") else None


def _tags(entry: Entry) -> list[str]:
    a = entry.analysis
    tags = [f"{a.sentiment.value}{' (PM)' if a.sentiment_source == 'pm' else ''}", a.status.value]
    tags.append(f"materiality {a.materiality}")
    if entry.status_changed and entry.previous_status:
        tags.append(f"was {entry.previous_status.value}")
    if not a.region_relevant:
        tags.append("outside UK/EMEA")
    return tags


# --- Markdown ----------------------------------------------------------------


def render_markdown(report: Report) -> str:
    lines = [f"# {title(report)}", "", f"_{_meta(report)}_", ""]
    if report.summary:
        lines += ["## Summary", "", *(f"- {_md_text(b)}" for b in report.summary), ""]

    rumours = report.rumours()
    lines += ["## Rumour watch", ""]
    lines += [line for e in rumours for line in _md_entry(e, show_brand=True)] or [
        "_No rumours today._"
    ]
    lines.append("")

    for sector in Sector:
        lines += [f"## {SECTOR_TITLE[sector]}", ""]
        quiet = []
        for name, brand in report.brands.items():
            if brand.sector is not sector:
                continue
            entries = report.for_brand(name)
            if not entries:
                quiet.append(name)
                continue
            lines += [f"### {name}", ""]
            for entry in entries:
                if entry.analysis.materiality >= MAJOR:
                    lines += _md_entry(entry, show_brand=False)
                else:
                    lines.append(
                        f"- Minor: {_md_headline(entry.story)} · {_md_sources(entry.story)}"
                    )
            lines.append("")
        if quiet:
            lines += [f"_No new coverage: {', '.join(quiet)}_", ""]

    if report.unanalysed:
        lines += ["## Not analysed", ""]
        lines += [f"- {_md_headline(s)} · {_md_sources(s)}" for s in report.unanalysed]
        lines.append("")
    lines += _md_footer(report)
    return "\n".join(lines) + "\n"


def _md_entry(entry: Entry, *, show_brand: bool) -> list[str]:
    a = entry.analysis
    brand = f"**{a.brand}** · " if show_brand else ""
    lines = [
        f"- {SENTIMENT_ICON[a.sentiment]} {brand}{_md_headline(entry.story)}",
        f"  {' · '.join(_tags(entry))} · `{entry.story.id}`  ",
        f"  {_md_text(a.why)}  ",
    ]
    if a.pm_note:
        lines.append(f"  PM note: {_md_text(a.pm_note)}  ")
    lines.append(f"  Sources: {_md_sources(entry.story)}")
    return lines


def _md_headline(story: Story) -> str:
    url = _safe_url(story.items[0].url)
    text = _md_text(story.title)
    return f"[{text}]({url})" if url else text


def _md_sources(story: Story) -> str:
    items = sources(story)
    links = []
    for item in items[:MAX_INLINE_SOURCES]:
        url = _safe_url(item.url)
        label = _md_text(item.source)
        links.append(f"[{label}]({url}) ({item.kind})" if url else f"{label} ({item.kind})")
    if len(items) > MAX_INLINE_SOURCES:
        links.append(f"+{len(items) - MAX_INLINE_SOURCES} more")
    return " · ".join(links)


def _md_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def _md_footer(report: Report) -> list[str]:
    lines = ["---", "", " · ".join(f"{label}: {n}" for label, n in report.stats.items()), ""]
    if report.unavailable:
        lines += [f"Sources unavailable this run: {', '.join(report.unavailable)}", ""]
    lines.append("Set your own sentiment with `demo sentiment set <id> positive|neutral|negative`.")
    return lines


def _meta(report: Report) -> str:
    return f"Last {report.window_hours} hours · generated {report.generated_at:%H:%M} UTC"


# --- HTML --------------------------------------------------------------------


def render_html(report: Report) -> str:
    e = html.escape
    parts = [f"<header><h1>{e(title(report))}</h1><p class='meta'>{e(_meta(report))}</p></header>"]
    if report.summary:
        bullets = "".join(f"<li>{e(b)}</li>" for b in report.summary)
        parts.append(f"<section class='summary'><h2>Summary</h2><ul>{bullets}</ul></section>")

    rumours = report.rumours()
    body = "".join(_html_entry(r, show_brand=True) for r in rumours) or (
        "<p class='quiet'>No rumours today.</p>"
    )
    parts.append(f"<section><h2>Rumour watch</h2>{body}</section>")

    for sector in Sector:
        blocks, quiet = [], []
        for name, brand in report.brands.items():
            if brand.sector is not sector:
                continue
            entries = report.for_brand(name)
            if not entries:
                quiet.append(name)
                continue
            major = [x for x in entries if x.analysis.materiality >= MAJOR]
            minor = [x for x in entries if x.analysis.materiality < MAJOR]
            block = f"<h3>{e(name)}</h3>" + "".join(_html_entry(x, show_brand=False) for x in major)
            if minor:
                rows = "".join(
                    f"<li>{_html_headline(x.story)} <span class='sources'>"
                    f"{_html_sources(x.story)}</span></li>"
                    for x in minor
                )
                block += f"<details><summary>{len(minor)} minor</summary><ul>{rows}</ul></details>"
            blocks.append(block)
        if quiet:
            blocks.append(f"<p class='quiet'>No new coverage: {e(', '.join(quiet))}</p>")
        parts.append(f"<section><h2>{SECTOR_TITLE[sector]}</h2>{''.join(blocks)}</section>")

    if report.unanalysed:
        rows = "".join(
            f"<li>{_html_headline(s)} <span class='sources'>{_html_sources(s)}</span></li>"
            for s in report.unanalysed
        )
        parts.append(f"<section><h2>Not analysed</h2><ul>{rows}</ul></section>")

    stats = " · ".join(f"{e(label)}: {n}" for label, n in report.stats.items())
    footer = f"<p>{stats}</p>"
    if report.unavailable:
        footer += f"<p>Sources unavailable this run: {e(', '.join(report.unavailable))}</p>"
    footer += (
        "<p>Set your own sentiment with "
        "<code>demo sentiment set &lt;id&gt; positive|neutral|negative</code>.</p>"
    )
    parts.append(f"<footer>{footer}</footer>")
    return HTML_PAGE.format(title=e(title(report)), css=CSS, body="".join(parts))


def _html_entry(entry: Entry, *, show_brand: bool) -> str:
    e = html.escape
    a = entry.analysis
    brand = f"<span class='brand'>{e(a.brand)}</span> " if show_brand else ""
    pm = " pm" if a.sentiment_source == "pm" else ""
    tags = "".join(f"<span class='tag'>{e(t)}</span>" for t in _tags(entry))
    note = f"<p class='note'>PM note: {e(a.pm_note)}</p>" if a.pm_note else ""
    return (
        f"<article class='entry {a.sentiment.value}{pm}'>"
        f"<p class='headline'><span class='icon' aria-label='{a.sentiment.value}'>"
        f"{SENTIMENT_ICON[a.sentiment]}</span> {brand}{_html_headline(entry.story)}</p>"
        f"<p class='tags'>{tags}<code>{e(entry.story.id)}</code></p>"
        f"<p class='why'>{e(a.why)}</p>{note}"
        f"<p class='sources'>{_html_sources(entry.story)}</p></article>"
    )


def _html_headline(story: Story) -> str:
    return _html_link(story.title, story.items[0].url)


def _html_sources(story: Story) -> str:
    items = sources(story)
    shown = " · ".join(_html_source(i) for i in items[:MAX_INLINE_SOURCES])
    hidden = items[MAX_INLINE_SOURCES:]
    if hidden:
        more = " · ".join(_html_source(i) for i in hidden)
        shown += f" <details class='more'><summary>+{len(hidden)} more</summary>{more}</details>"
    return shown


def _html_source(item: RawItem) -> str:
    return f"{_html_link(item.source, item.url)} <span class='kind'>{item.kind}</span>"


def _html_link(text: str, url: str) -> str:
    safe = _safe_url(url)
    label = html.escape(text)
    if safe is None:
        return label
    return f"<a href='{html.escape(safe, quote=True)}' rel='noopener'>{label}</a>"


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body><main>{body}</main></body>
</html>
"""

CSS = """
:root {
  --bg: #fbfaf7; --fg: #1d1c1a; --muted: #6b6760; --line: #e4e0d8; --card: #ffffff;
  --pos: #1f7a4d; --neg: #b3261e; --neu: #6b6760; --accent: #8a6d3b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161514; --fg: #ece9e4; --muted: #a39e95; --line: #34312d; --card: #1f1d1b;
    --pos: #5cc28e; --neg: #f08a80; --neu: #a39e95; --accent: #d1b178;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 32px 0 12px; padding-bottom: 6px;
  border-bottom: 1px solid var(--line); }
h3 { font-size: 1rem; margin: 20px 0 8px; color: var(--accent); }
a { color: inherit; text-decoration-color: var(--line); text-underline-offset: 2px; }
a:hover { text-decoration-color: currentColor; }
.meta, .quiet, footer { color: var(--muted); font-size: 0.875rem; }
.summary ul { padding-left: 20px; }
.summary li { margin-bottom: 6px; }
.entry { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--neu);
  border-radius: 6px; padding: 10px 14px; margin: 10px 0; }
.entry.positive { border-left-color: var(--pos); }
.entry.negative { border-left-color: var(--neg); }
.entry p { margin: 4px 0; }
.headline { font-weight: 600; }
.icon { display: inline-block; width: 1.1em; }
.positive .icon { color: var(--pos); } .negative .icon { color: var(--neg); }
.neutral .icon { color: var(--neu); }
.brand { color: var(--accent); }
.tags { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.tag { font-size: 0.75rem; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line);
  color: var(--muted); }
.pm .tag:first-child { border-color: var(--accent); color: var(--accent); font-weight: 600; }
code { font-size: 0.75rem; color: var(--muted); }
.why { margin-top: 6px; }
.note { font-style: italic; color: var(--accent); }
.sources { font-size: 0.85rem; color: var(--muted); }
.kind { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.8; }
details { margin: 6px 0; }
details.more { display: inline; }
summary { cursor: pointer; color: var(--muted); font-size: 0.85rem; }
details ul { padding-left: 20px; margin: 6px 0; }
details li { margin: 4px 0; }
footer { margin-top: 40px; border-top: 1px solid var(--line); padding-top: 12px; }
"""
