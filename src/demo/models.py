from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal


class Sector(StrEnum):
    LUXURY = "luxury"
    SPORTSWEAR = "sportswear"


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class Status(StrEnum):
    RUMOUR = "rumour"
    CONFIRMED = "confirmed"
    DENIED = "denied"


ItemKind = Literal["news", "social", "web"]
SentimentSource = Literal["model", "pm"]


@dataclass(frozen=True)
class Brand:
    name: str
    sector: Sector
    ticker: str | None
    aliases: tuple[str, ...] = ()
    scout: bool = True  # also search the web for this brand each run


@dataclass(frozen=True)
class RawItem:
    source: str
    kind: ItemKind
    title: str
    url: str
    published: datetime
    text: str = ""


@dataclass
class Story:
    """One news story, merged from duplicate items across sources."""

    id: str
    title: str
    items: list[RawItem]


@dataclass
class Analysis:
    story_id: str
    brand: str
    region_relevant: bool
    status: Status
    sentiment: Sentiment
    materiality: int  # 1 (noise) to 5 (thesis-changing)
    why: str
    sentiment_source: SentimentSource = "model"
    pm_note: str | None = None
