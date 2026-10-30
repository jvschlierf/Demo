from typing import Any

import anthropic

from demo.claude import structured_call
from demo.models import Analysis, Story

MIN_MATERIALITY = 3
MAX_BULLETS = 6

SUMMARY_SYSTEM = """\
You write the top of a morning brief for a portfolio manager covering luxury and sportswear \
companies from the UK/EMEA. Write one short bullet per distinct story that matters, most \
material first, and at most 6. Flag rumours worth watching as rumours, and say which company \
each bullet is about. If only one or two stories matter, write only one or two bullets: never \
repeat a story or add filler, such as noting that there are no rumours. Where the PM has set \
the sentiment themselves (marked "PM view"), use their sentiment, not your own. Use only the \
facts given. Do not comment on share prices, ratings or valuation."""

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"bullets": {"type": "array", "items": {"type": "string"}}},
    "required": ["bullets"],
    "additionalProperties": False,
}


def summarise(
    analyses: list[Analysis], stories: dict[str, Story], client: anthropic.Anthropic
) -> list[str]:
    """Executive-summary bullets; empty when nothing reaches the materiality bar."""
    notable = sorted(
        (a for a in analyses if a.materiality >= MIN_MATERIALITY),
        key=lambda a: a.materiality,
        reverse=True,
    )
    if not notable:
        return []
    data = structured_call(
        client,
        system=SUMMARY_SYSTEM,
        prompt="\n".join(_line(a, stories[a.story_id]) for a in notable),
        schema=SUMMARY_SCHEMA,
        effort="medium",
    )
    return data["bullets"][:MAX_BULLETS] if data else []


def _line(analysis: Analysis, story: Story) -> str:
    view = "PM view" if analysis.sentiment_source == "pm" else "model view"
    note = f" (PM note: {analysis.pm_note})" if analysis.pm_note else ""
    return (
        f"- {analysis.brand} | {analysis.status.value} | {analysis.sentiment.value} ({view})"
        f" | materiality {analysis.materiality} | {story.title} | {analysis.why}{note}"
    )
