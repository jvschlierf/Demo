import html
import logging
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from demo.brands import match_brands
from demo.models import Brand, ItemKind, RawItem

log = logging.getLogger(__name__)

USER_AGENT = "demo-news-tracker/0.1"
TIMEOUT_SECONDS = 10
ATOM = "{http://www.w3.org/2005/Atom}"

SUBREDDITS = ("Sneakers", "fashion", "handbags", "UKInvesting")

# The PM already follows prices, ratings and holdings closely, so market-data items are
# dropped and the report covers business news only.
BLOCKED_SOURCES = frozenset(
    {
        "Alphaville e Arredores",  # product listings, not news
        "ad-hoc-news.de",
        "Idéal Investisseur",
        "MarketBeat",
        "Moomoo",
        "Seeking Alpha",
        "Simply Wall St",
        "Traders Union",
        "TradingView",
        "Trefis",
    }
)
MARKET_DATA_TITLE = re.compile(
    r"""
    \bstocks?\b | \bshare\ price
    | \bshares?\ (?:up|down|rise|rises|rose|fall|falls|fell|gain|gains|jump|jumps|slide|slides
                   |slip|slips|drop|drops|climb|climbs|sink|sinks|tumble|tumbles|surge|surges
                   |soar|soars|rally|rallies)\b
    | price\ target | target\ price | short\ interest | moving\ average | seasonal\ returns
    | \b(?:buy|sell|hold|neutral|overweight|underweight)\ rating | \brated\ "?(?:buy|hold|sell)
    | \b(?:under|over)valued\b
    | \bshareholders\ who
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    kind: ItemKind


TRADE_FEEDS = (
    Feed("Guardian Business", "https://www.theguardian.com/uk/business/rss", "news"),
    Feed(
        "Business of Fashion",
        "https://www.businessoffashion.com/arc/outboundfeeds/rss/?outputType=xml",
        "news",
    ),
    Feed("FashionUnited UK", "https://fashionunited.uk/rss-news", "news"),
    Feed("Drapers", "https://www.drapersonline.com/rss", "news"),
)


@dataclass
class CollectResult:
    items: list[RawItem]
    unavailable: list[str]  # names of feeds that failed this run


class SourceError(Exception):
    pass


Fetcher = Callable[[str], bytes]


def google_news_feed(brand: Brand, days: int = 1) -> Feed:
    terms = " OR ".join(f'"{term}"' for term in (brand.name, *brand.aliases))
    query = quote_plus(f"{terms} when:{days}d")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-GB&gl=GB&ceid=GB:en"
    return Feed(f"Google News: {brand.name}", url, "news")


def reddit_feed(subreddits: tuple[str, ...] = SUBREDDITS) -> Feed:
    # One combined request: Reddit rate-limits unauthenticated clients quickly.
    url = f"https://www.reddit.com/r/{'+'.join(subreddits)}/new/.rss?limit=100"
    return Feed("Reddit", url, "social")


def default_feeds(brands: dict[str, Brand], days: int = 1) -> list[Feed]:
    return [
        *(google_news_feed(brand, days) for brand in brands.values()),
        *TRADE_FEEDS,
        reddit_feed(),
    ]


def fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except (URLError, TimeoutError) as e:
        raise SourceError(f"{url}: {e}") from e


def parse_feed(data: bytes, feed: Feed) -> list[RawItem]:
    """Parse an RSS 2.0 or Atom document, skipping entries without a title, link or date."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise SourceError(f"{feed.name}: malformed XML: {e}") from e

    if root.tag == "rss":
        entries = (_parse_rss_item(el, feed) for el in root.iter("item"))
    elif root.tag == f"{ATOM}feed":
        entries = (_parse_atom_entry(el, feed) for el in root.iter(f"{ATOM}entry"))
    else:
        raise SourceError(f"{feed.name}: unrecognised feed format <{root.tag}>")
    return [item for item in entries if item is not None]


def _parse_rss_item(el: ET.Element, feed: Feed) -> RawItem | None:
    title, link = _text(el, "title"), _text(el, "link")
    published = _parse_rfc822(_text(el, "pubDate"))
    if not (title and link and published):
        return None
    # Aggregators like Google News name the original outlet in <source>.
    source = _text(el, "source") or feed.name
    title = title.removesuffix(f" - {source}")
    return RawItem(source, feed.kind, title, link, published, _clean_html(_text(el, "description")))


def _parse_atom_entry(el: ET.Element, feed: Feed) -> RawItem | None:
    title = _text(el, f"{ATOM}title")
    link_el = el.find(f"{ATOM}link")
    link = link_el.get("href", "") if link_el is not None else ""
    published = _parse_iso(_text(el, f"{ATOM}published") or _text(el, f"{ATOM}updated"))
    if not (title and link and published):
        return None
    # Reddit labels each entry with its subreddit, e.g. "r/Sneakers".
    category = el.find(f"{ATOM}category")
    source = (category.get("label") if category is not None else None) or feed.name
    text = _clean_html(_text(el, f"{ATOM}content"))
    return RawItem(source, feed.kind, title, link, published, text)


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _parse_rfc822(value: str) -> datetime | None:
    try:
        return _as_utc(parsedate_to_datetime(value))
    except (TypeError, ValueError, IndexError):
        return None


def _parse_iso(value: str) -> datetime | None:
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _clean_html(raw: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return " ".join(text.split())


def collect(
    brands: dict[str, Brand],
    since: datetime,
    *,
    feeds: list[Feed] | None = None,
    fetcher: Fetcher = fetch,
    max_workers: int = 6,
) -> CollectResult:
    """Fetch all feeds concurrently; keep items newer than `since` that mention a tracked brand."""
    if feeds is None:
        days = max(1, math.ceil((datetime.now(UTC) - since).total_seconds() / 86400))
        feeds = default_feeds(brands, days)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(lambda feed: _load(feed, fetcher), feeds))

    items: list[RawItem] = []
    unavailable: list[str] = []
    for feed, result in zip(feeds, results, strict=True):
        if isinstance(result, SourceError):
            log.warning("Feed unavailable: %s", result)
            unavailable.append(feed.name)
            continue
        items.extend(i for i in result if i.published >= since and is_relevant(i, brands))
    return CollectResult(items, unavailable)


def is_relevant(item: RawItem, brands: dict[str, Brand]) -> bool:
    """Not from a blocked source, not market data, and mentions a tracked brand."""
    return (
        item.source not in BLOCKED_SOURCES
        and not MARKET_DATA_TITLE.search(item.title)
        and bool(match_brands(f"{item.title} {item.text}", brands))
    )


def _load(feed: Feed, fetcher: Fetcher) -> list[RawItem] | SourceError:
    try:
        return parse_feed(fetcher(feed.url), feed)
    except SourceError as e:
        return e
