import dataclasses
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from demo.dedupe import story_id
from demo.models import Analysis, Sentiment, Status, Story

DEFAULT_DB_PATH = Path("data/tracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
-- Every item ever seen in a story, so a story keeps its id when it is re-clustered later.
CREATE TABLE IF NOT EXISTS story_items (
    item_id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL REFERENCES stories(id)
);
CREATE TABLE IF NOT EXISTS analyses (
    story_id TEXT NOT NULL REFERENCES stories(id),
    brand TEXT NOT NULL,
    run_date TEXT NOT NULL,
    region_relevant INTEGER NOT NULL,
    status TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    materiality INTEGER NOT NULL,
    why TEXT NOT NULL,
    PRIMARY KEY (story_id, brand, run_date)
);
CREATE TABLE IF NOT EXISTS overrides (
    story_id TEXT NOT NULL REFERENCES stories(id),
    brand TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    note TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (story_id, brand)
);
"""


class OverrideError(ValueError):
    pass


@dataclass(frozen=True)
class Override:
    story_id: str
    brand: str
    title: str
    sentiment: Sentiment
    note: str | None
    updated_at: datetime


class Store:
    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def save_stories(self, stories: list[Story], seen_at: datetime) -> None:
        """Persist stories; a story containing a previously seen item keeps that story's id."""
        with self._conn:
            for story in stories:
                item_ids = {story.id} | {story_id(item) for item in story.items}
                story.id = self._known_story_id(item_ids) or story.id
                self._conn.execute(
                    "INSERT INTO stories (id, title, first_seen, last_seen) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET title = excluded.title,"
                    " last_seen = excluded.last_seen",
                    (story.id, story.title, seen_at.isoformat(), seen_at.isoformat()),
                )
                self._conn.executemany(
                    "INSERT OR IGNORE INTO story_items (item_id, story_id) VALUES (?, ?)",
                    [(item_id, story.id) for item_id in item_ids],
                )

    def _known_story_id(self, item_ids: set[str]) -> str | None:
        placeholders = ",".join("?" * len(item_ids))
        row = self._conn.execute(
            f"SELECT story_id FROM story_items WHERE item_id IN ({placeholders}) LIMIT 1",
            sorted(item_ids),
        ).fetchone()
        return row[0] if row else None

    def save_analyses(self, analyses: list[Analysis], run_date: date) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO analyses (story_id, brand, run_date, region_relevant,"
                " status, sentiment, materiality, why) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        a.story_id,
                        a.brand,
                        run_date.isoformat(),
                        a.region_relevant,
                        a.status.value,
                        a.sentiment.value,
                        a.materiality,
                        a.why,
                    )
                    for a in analyses
                ],
            )

    def previous_status(self, story_id: str, brand: str, before: date) -> Status | None:
        row = self._conn.execute(
            "SELECT status FROM analyses WHERE story_id = ? AND brand = ? AND run_date < ?"
            " ORDER BY run_date DESC LIMIT 1",
            (story_id, brand, before.isoformat()),
        ).fetchone()
        return Status(row[0]) if row else None

    def brands_for(self, story_id: str) -> list[str]:
        """Brands the story has been analysed for; raises OverrideError for an unknown story."""
        if not self._conn.execute("SELECT 1 FROM stories WHERE id = ?", (story_id,)).fetchone():
            raise OverrideError(f"unknown story id {story_id!r}")
        rows = self._conn.execute(
            "SELECT DISTINCT brand FROM analyses WHERE story_id = ? ORDER BY brand", (story_id,)
        )
        return [brand for (brand,) in rows]

    def set_override(
        self,
        story_id: str,
        sentiment: Sentiment,
        brand: str | None = None,
        note: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Record the PM's sentiment for one brand in a story; returns the brand it applied to."""
        brand = self._resolve_brand(story_id, brand)
        updated_at = (now or datetime.now(UTC)).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO overrides (story_id, brand, sentiment, note, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (story_id, brand, sentiment.value, note, updated_at),
            )
        return brand

    def _resolve_brand(self, story_id: str, brand: str | None) -> str:
        brands = self.brands_for(story_id)
        if not brands:
            raise OverrideError(f"story {story_id!r} has not been analysed yet")
        if brand is None:
            if len(brands) > 1:
                raise OverrideError(
                    f"story {story_id!r} covers several brands ({', '.join(brands)});"
                    " choose one with --brand"
                )
            return brands[0]
        match = next((b for b in brands if b.casefold() == brand.casefold()), None)
        if match is None:
            raise OverrideError(
                f"story {story_id!r} has no analysis for {brand!r}"
                f" (analysed brands: {', '.join(brands)})"
            )
        return match

    def overrides(self) -> list[Override]:
        rows = self._conn.execute(
            "SELECT o.story_id, o.brand, s.title, o.sentiment, o.note, o.updated_at"
            " FROM overrides o JOIN stories s ON s.id = o.story_id"
            " ORDER BY o.updated_at DESC"
        )
        return [
            Override(sid, brand, title, Sentiment(sentiment), note, datetime.fromisoformat(ts))
            for sid, brand, title, sentiment, note, ts in rows
        ]


def apply_overrides(analyses: list[Analysis], overrides: list[Override]) -> list[Analysis]:
    """Return analyses with the PM's sentiment taking precedence over the model's."""
    by_key = {(o.story_id, o.brand): o for o in overrides}
    result = []
    for analysis in analyses:
        override = by_key.get((analysis.story_id, analysis.brand))
        if override:
            analysis = dataclasses.replace(
                analysis,
                sentiment=override.sentiment,
                sentiment_source="pm",
                pm_note=override.note,
            )
        result.append(analysis)
    return result
