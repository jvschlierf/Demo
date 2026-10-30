import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from demo.brands import match_brands
from demo.models import Brand, RawItem, Story

STOPWORDS = frozenset(
    "the a an and or of to in on for with at by from as is are was be its it this that "
    "after over amid into says say said new".split()
)
# Two headlines are the same story if they share at least this many content words,
# and those shared words make up this fraction of the shorter headline.
MIN_SHARED_WORDS = 3
MIN_OVERLAP = 0.6


def story_id(item: RawItem) -> str:
    """Stable ID derived from the normalised headline, so it survives URL and time changes."""
    normalised = " ".join(re.findall(r"\w+", item.title.casefold()))
    return hashlib.sha1(normalised.encode()).hexdigest()[:12]


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return parts.netloc.lower().removeprefix("www.") + parts.path.rstrip("/")


@dataclass
class _Cluster:
    items: list[RawItem] = field(default_factory=list)
    urls: set[str] = field(default_factory=set)
    word_sets: list[frozenset[str]] = field(default_factory=list)
    brands: set[str] = field(default_factory=set)

    def matches(self, url: str, words: frozenset[str], brands: set[str]) -> bool:
        if url in self.urls:
            return True
        # Similar headlines about different firms (e.g. earnings templates) are different stories.
        return bool(self.brands & brands) and any(_similar(words, w) for w in self.word_sets)


def cluster(items: list[RawItem], brands: dict[str, Brand]) -> list[Story]:
    """Merge items reporting the same story; stories are returned in order first seen."""
    clusters: list[_Cluster] = []
    for item in sorted(items, key=lambda i: (i.published, i.url)):
        url, words = canonical_url(item.url), _words(item.title)
        mentioned = set(match_brands(f"{item.title} {item.text}", brands))
        match = next((c for c in clusters if c.matches(url, words, mentioned)), None)
        if match is None:
            match = _Cluster()
            clusters.append(match)
        elif url in match.urls:
            continue  # same article, returned by another brand's feed
        match.items.append(item)
        match.urls.add(url)
        match.word_sets.append(words)
        match.brands |= mentioned
    return [_to_story(c.items) for c in clusters]


def merge_stories(stories: list[Story]) -> Story:
    """Combine stories judged to be the same event into one, dropping repeated articles."""
    items: list[RawItem] = []
    seen: set[str] = set()
    for item in sorted((i for s in stories for i in s.items), key=lambda i: (i.published, i.url)):
        url = canonical_url(item.url)
        if url not in seen:
            seen.add(url)
            items.append(item)
    return _to_story(items)


def _to_story(items: list[RawItem]) -> Story:
    # Prefer a news headline over a social post title when one exists.
    headline = next((i for i in items if i.kind != "social"), items[0])
    return Story(id=story_id(items[0]), title=headline.title, items=items)


def _words(title: str) -> frozenset[str]:
    return frozenset(
        w for w in re.findall(r"\w+", title.casefold()) if len(w) > 1 and w not in STOPWORDS
    )


def _similar(a: frozenset[str], b: frozenset[str]) -> bool:
    shared = len(a & b)
    return shared >= MIN_SHARED_WORDS and shared / min(len(a), len(b)) >= MIN_OVERLAP
