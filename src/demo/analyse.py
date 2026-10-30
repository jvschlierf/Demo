import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import anthropic

from demo.brands import match_brands
from demo.claude import structured_call
from demo.dedupe import merge_stories
from demo.models import Analysis, Brand, Sentiment, Status, Story

log = logging.getLogger(__name__)

SCREEN_BATCH_SIZE = 50
MAX_ITEMS_PER_STORY = 8
MAX_TEXT_CHARS = 600

SCREEN_SYSTEM = """\
You screen news headlines for a portfolio manager covering listed luxury and sportswear \
companies from the UK/EMEA. Keep only stories that could matter to a fundamental investor in a \
tracked company:
- management changes, strategy, results, guidance, trading updates, restructuring
- M&A, licensing, partnerships, store or market entries and exits
- legal, regulatory, tax, tariff, labour, supply-chain or ESG issues and scandals
- demand and brand-heat signals with real scale (e.g. a major collaboration or sell-out trend)
- rumours and unconfirmed reports about any of the above, including social-media chatter

Drop:
- share-price moves, analyst ratings, price targets, valuation pieces and other market data \
(the PM already follows these closely)
- shopping deals, product release dates, "where to buy" pieces and resale listings
- celebrity or fashion editorial with no business angle
- stories where the brand name refers to something else (a person, another company)

Each headline has an index. Several headlines often report the same event in different words \
(e.g. "Adidas raises outlook" and "Adidas sees Europe returning to growth" from the same \
results release). Return one group per distinct event you keep, listing the indices of every \
headline about that event. Use each index at most once; leave out the indices you drop."""

ANALYSE_SYSTEM = """\
You analyse one news story for a portfolio manager covering luxury and sportswear companies \
from the UK/EMEA. Return one analysis per tracked company the story is materially about; \
return an empty list if it is not really about any tracked company (e.g. a namesake).

For each company:
- region_relevant: true if the story concerns the company's UK/EMEA business, consumers, \
regulators or listing, or is a group-level event (management, guidance, M&A) that affects it.
- status: "rumour" for unconfirmed reports ("sources say", "reportedly", "in talks", social \
chatter); "confirmed" for company announcements or established facts; "denied" if the company \
has denied it.
- sentiment: the story's likely effect on that company's business, brand or reputation - \
"positive", "neutral" or "negative". Judge each company separately: a win for one company can \
be negative for a rival. A labour or supply-chain scandal is negative for the company involved.
- materiality: 1 = noise, 2 = minor, 3 = worth knowing, 4 = could change estimates, \
5 = thesis-changing.
- why: one sentence on why it matters to an investor in that company.

Use only facts in the provided text. Do not comment on share prices, ratings or valuation."""


@dataclass
class AnalysisResult:
    analyses: list[Analysis]
    unanalysed: list[Story]  # stories Claude could not analyse; still listed in the report


def screen(
    stories: list[Story],
    brands: dict[str, Brand],
    client: anthropic.Anthropic,
    *,
    batch_size: int = SCREEN_BATCH_SIZE,
    max_workers: int = 8,
) -> list[Story]:
    """Cheap first pass over headlines in batches: drops noise and merges same-event stories.

    Stories are batched by brand so that reworded duplicates land in the same batch.
    """
    ordered = sorted(stories, key=lambda s: (match_brands(_story_text(s), brands)[:1], s.title))
    batches = [ordered[i : i + batch_size] for i in range(0, len(ordered), batch_size)]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        kept = pool.map(lambda batch: _screen_batch(batch, brands, client), batches)
    return [story for batch in kept for story in batch]


def _screen_batch(
    batch: list[Story], brands: dict[str, Brand], client: anthropic.Anthropic
) -> list[Story]:
    lines = []
    for index, story in enumerate(batch):
        mentioned = ", ".join(match_brands(_story_text(story), brands))
        sources = ", ".join(sorted({item.source for item in story.items}))
        lines.append(f"[{index}] {story.title} ({mentioned}; {sources})")
    data = structured_call(
        client,
        system=SCREEN_SYSTEM,
        prompt="\n".join(lines),
        schema=SCREEN_SCHEMA,
        effort="low",
    )
    if data is None:
        log.warning("Screen failed for a batch of %d stories; keeping them all", len(batch))
        return batch
    kept: list[Story] = []
    used: set[int] = set()
    for group in data["groups"]:
        indices = [i for i in dict.fromkeys(group) if 0 <= i < len(batch) and i not in used]
        if len(indices) == 1:
            kept.append(batch[indices[0]])
        elif indices:
            kept.append(merge_stories([batch[i] for i in indices]))
        used.update(indices)
    return kept


def analyse_stories(
    stories: list[Story],
    brands: dict[str, Brand],
    client: anthropic.Anthropic,
    *,
    max_workers: int = 8,
) -> AnalysisResult:
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(lambda story: analyse_story(story, brands, client), stories))
    analyses = [a for result in results if result is not None for a in result]
    unanalysed = [story for story, result in zip(stories, results, strict=True) if result is None]
    return AnalysisResult(analyses, unanalysed)


def analyse_story(
    story: Story, brands: dict[str, Brand], client: anthropic.Anthropic
) -> list[Analysis] | None:
    """Analyse one story per tracked brand it covers; None if Claude gave no valid answer."""
    data = structured_call(
        client,
        system=ANALYSE_SYSTEM,
        prompt=_story_prompt(story, brands),
        schema=_analysis_schema(list(brands)),
        effort="low",
    )
    if data is None:
        return None
    try:
        return [_to_analysis(story.id, entry, brands) for entry in data["analyses"]]
    except (KeyError, ValueError) as e:
        log.warning("Invalid analysis for story %s: %s", story.id, e)
        return None


def _to_analysis(story_id: str, entry: dict[str, Any], brands: dict[str, Brand]) -> Analysis:
    if entry["brand"] not in brands:
        raise ValueError(f"unknown brand {entry['brand']!r}")
    materiality = int(entry["materiality"])
    if not 1 <= materiality <= 5:
        raise ValueError(f"materiality {materiality} out of range")
    return Analysis(
        story_id=story_id,
        brand=entry["brand"],
        region_relevant=bool(entry["region_relevant"]),
        status=Status(entry["status"]),
        sentiment=Sentiment(entry["sentiment"]),
        materiality=materiality,
        why=entry["why"],
    )


def _story_prompt(story: Story, brands: dict[str, Brand]) -> str:
    mentioned = ", ".join(match_brands(_story_text(story), brands)) or "none matched"
    lines = [
        f"Tracked companies: {', '.join(brands)}",
        f"Companies mentioned by keyword: {mentioned}",
        f"Story: {story.title}",
        f"Reported by {len(story.items)} source(s):",
    ]
    for item in story.items[:MAX_ITEMS_PER_STORY]:
        lines.append(
            f"- {item.source} ({item.kind}, {item.published:%Y-%m-%d %H:%M} UTC): {item.title}"
        )
        if item.text:
            lines.append(f"  {item.text[:MAX_TEXT_CHARS]}")
    return "\n".join(lines)


def _story_text(story: Story) -> str:
    return " ".join(f"{item.title} {item.text}" for item in story.items)


SCREEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}
    },
    "required": ["groups"],
    "additionalProperties": False,
}


def _analysis_schema(brand_names: list[str]) -> dict[str, Any]:
    entry = {
        "type": "object",
        "properties": {
            "brand": {"type": "string", "enum": brand_names},
            "region_relevant": {"type": "boolean"},
            "status": {"type": "string", "enum": [s.value for s in Status]},
            "sentiment": {"type": "string", "enum": [s.value for s in Sentiment]},
            "materiality": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
            "why": {"type": "string"},
        },
        "required": ["brand", "region_relevant", "status", "sentiment", "materiality", "why"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"analyses": {"type": "array", "items": entry}},
        "required": ["analyses"],
        "additionalProperties": False,
    }
