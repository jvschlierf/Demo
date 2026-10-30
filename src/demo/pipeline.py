from collections.abc import Callable
from datetime import datetime, timedelta

import anthropic

from demo.analyse import analyse_stories, screen
from demo.dedupe import cluster
from demo.models import Brand
from demo.report import Entry, Report
from demo.scout import scout_all
from demo.sources import Fetcher, collect, fetch
from demo.store import Store, apply_overrides
from demo.summarise import summarise


def build_report(
    brands: dict[str, Brand],
    store: Store,
    client: anthropic.Anthropic,
    *,
    now: datetime,
    window: timedelta,
    web: bool = True,
    fetcher: Fetcher = fetch,
    progress: Callable[[str], None] = lambda message: None,
) -> Report:
    """Collect, screen, analyse and summarise the window's news into a report."""
    since = now - window
    progress("Collecting news feeds")
    collected = collect(brands, since, fetcher=fetcher)
    items, unavailable = list(collected.items), list(collected.unavailable)
    web_items = 0
    if web:
        progress(f"Searching the web for {sum(b.scout for b in brands.values())} brands")
        scouted = scout_all(brands, since, client)
        items += scouted.items
        unavailable += scouted.unavailable
        web_items = len(scouted.items)

    stories = cluster(items, brands)
    progress(f"Screening {len(stories)} stories")
    kept = screen(stories, brands, client)
    store.save_stories(kept, now)  # gives re-seen stories their existing ids

    progress(f"Analysing {len(kept)} stories")
    result = analyse_stories(kept, brands, client)
    store.save_analyses(result.analyses, now.date())
    analyses = apply_overrides(result.analyses, store.overrides())
    by_id = {story.id: story for story in kept}

    progress("Writing summary")
    summary = summarise(analyses, by_id, client)
    entries = [
        Entry(a, by_id[a.story_id], store.previous_status(a.story_id, a.brand, now.date()))
        for a in analyses
    ]
    return Report(
        day=now.date(),
        generated_at=now,
        window_hours=round(window.total_seconds() / 3600),
        brands=brands,
        summary=summary,
        entries=entries,
        unanalysed=result.unanalysed,
        unavailable=unavailable,
        stats={
            "Feed items": len(collected.items),
            "Web items": web_items,
            "Stories": len(stories),
            "Kept after screening": len(kept),
        },
    )
