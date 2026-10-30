import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import anthropic

from demo.claude import FALLBACK_BETA, MODEL
from demo.models import Brand, RawItem
from demo.sources import CollectResult, is_relevant

log = logging.getLogger(__name__)

# The basic search tool. web_search_20260209 runs searches from a code sandbox; in testing it
# used more tokens and spent its search allowance on failed scripts.
SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
    "user_location": {"type": "approximate", "country": "GB", "city": "London"},
}
MAX_CONTINUATIONS = 2
# page_age is reported in whole days ("1 days ago"), so allow a day of slack on the window.
PAGE_AGE_SLACK = timedelta(days=1)

SCOUT_SYSTEM = """\
You search the web for a portfolio manager covering luxury and sportswear companies from the \
UK/EMEA. Look for recent reports a fundamental investor would want, especially rumours and \
unconfirmed reports: talks, takeovers, management changes, profit warnings, boycotts, \
supply-chain or labour issues, leaks. Try searches such as "<company> reportedly", \
"<company> sources say" and "<company> rumour". Skip share-price moves, analyst ratings and \
valuation pieces, shopping deals and product release dates.

Return each relevant finding with the exact URL of the search result it came from, a \
one-sentence summary of the facts it reports, and whether it is unconfirmed. Return an empty \
list if nothing recent and relevant turns up."""

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "summary": {"type": "string"},
                    "unconfirmed": {"type": "boolean"},
                },
                "required": ["url", "summary", "unconfirmed"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

_RELATIVE_AGE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)
_AGE_UNITS = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
}


def scout_all(
    brands: dict[str, Brand],
    since: datetime,
    client: anthropic.Anthropic,
    *,
    max_workers: int = 6,
) -> CollectResult:
    """Web-search every brand marked `scout = true`; failures are reported, not raised."""
    targets = [brand for brand in brands.values() if brand.scout]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(lambda brand: _scout_safely(brand, since, client), targets))

    items: list[RawItem] = []
    unavailable: list[str] = []
    for brand, result in zip(targets, results, strict=True):
        if result is None:
            unavailable.append(f"Web search: {brand.name}")
        else:
            items.extend(item for item in result if is_relevant(item, brands))
    return CollectResult(items, unavailable)


def _scout_safely(
    brand: Brand, since: datetime, client: anthropic.Anthropic
) -> list[RawItem] | None:
    try:
        return scout_brand(brand, since, client)
    except anthropic.APIError as e:
        log.warning("Web search failed for %s: %s", brand.name, e)
        return None


def scout_brand(
    brand: Brand, since: datetime, client: anthropic.Anthropic, now: datetime | None = None
) -> list[RawItem] | None:
    """Find recent web reports about one brand; None if Claude gave no usable answer.

    Only URLs that appear in the actual search results become items, never URLs from the
    model's own text, and only if the result's page age falls inside the window.
    """
    now = now or datetime.now(UTC)
    question = {"role": "user", "content": _prompt(brand, since, now)}
    transcript: list[Any] = []
    results: dict[str, tuple[str, datetime | None]] = {}
    for _ in range(MAX_CONTINUATIONS + 1):
        messages = (
            [question, {"role": "assistant", "content": transcript}] if transcript else [question]
        )
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=[{"type": "text", "text": SCOUT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[SEARCH_TOOL],
            messages=messages,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": FINDINGS_SCHEMA},
            },
        )
        results |= parse_search_results(response, now)
        if response.stop_reason != "pause_turn":
            break
        # A long server-side search loop pauses; re-sending the transcript resumes it.
        transcript = [*transcript, *response.content]

    if response.stop_reason != "end_turn":
        log.warning("Web search for %s stopped with %s", brand.name, response.stop_reason)
        return None
    texts = [block.text for block in response.content if block.type == "text"]
    try:
        findings = json.loads(texts[-1])["findings"]
    except (IndexError, KeyError, json.JSONDecodeError):
        log.warning("Web search for %s returned no valid findings", brand.name)
        return None

    items = []
    for finding in findings:
        url = finding["url"]
        if url not in results:
            log.info("Ignoring %s: not among the search results", url)
            continue
        title, published = results[url]
        if published is None or published < since - PAGE_AGE_SLACK:
            continue
        prefix = "Unconfirmed report: " if finding["unconfirmed"] else ""
        items.append(RawItem(_site(url), "web", title, url, published, prefix + finding["summary"]))
    return items


def parse_search_results(response: Any, now: datetime) -> dict[str, tuple[str, datetime | None]]:
    """Map each search-result URL in a response to its title and estimated publish time."""
    results: dict[str, tuple[str, datetime | None]] = {}
    for block in response.content:
        if block.type != "web_search_tool_result":
            continue
        if not isinstance(block.content, list):  # an error object, e.g. max_uses_exceeded
            log.warning("Web search error: %s", getattr(block.content, "error_code", block.content))
            continue
        for result in block.content:
            results[result.url] = (result.title, parse_page_age(result.page_age, now))
    return results


def parse_page_age(page_age: str | None, now: datetime) -> datetime | None:
    """Turn "3 days ago" or "April 30, 2026" into a datetime; None if absent or unrecognised."""
    if not page_age:
        return None
    if match := _RELATIVE_AGE.search(page_age):
        return now - int(match.group(1)) * _AGE_UNITS[match.group(2).lower()]
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(page_age.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def _prompt(brand: Brand, since: datetime, now: datetime) -> str:
    hours = max(1, round((now - since).total_seconds() / 3600))
    company = f"Company: {brand.name}"
    if brand.aliases:
        company += f" (brands and people: {', '.join(brand.aliases)})"
    return f"{company}\nFind reports from the last {hours} hours, as of {now:%Y-%m-%d %H:%M} UTC."


def _site(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")
