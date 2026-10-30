from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from demo.dedupe import story_id
from demo.models import Analysis, RawItem, Sentiment, Status, Story
from demo.store import OverrideError, Store, apply_overrides

DAY1 = date(2026, 10, 30)
DAY2 = date(2026, 10, 31)


def raw(title: str, hour: int = 8) -> RawItem:
    published = datetime(2026, 10, 30, hour, tzinfo=UTC)
    return RawItem("Reuters", "news", title, f"https://a.test/{hour}", published)


def make_story(*titles: str) -> Story:
    items = [raw(t, hour) for hour, t in enumerate(titles, start=6)]
    return Story(story_id(items[0]), titles[0], items)


def analysis(sid: str, brand: str, sentiment: Sentiment = Sentiment.NEUTRAL) -> Analysis:
    return Analysis(sid, brand, True, Status.RUMOUR, sentiment, 3, "why")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "tracker.db")
    yield s
    s.close()


def saved_story(store: Store, *brands: str, title: str = "Burberry weighs licence sale") -> str:
    story = make_story(title)
    store.save_stories([story], datetime(2026, 10, 30, 9, tzinfo=UTC))
    store.save_analyses([analysis(story.id, b) for b in brands], DAY1)
    return story.id


def test_fresh_store_has_no_overrides(store: Store) -> None:
    store.save_stories([], datetime.now(UTC))
    assert store.overrides() == []


def test_set_override_for_single_brand_story(store: Store) -> None:
    sid = saved_story(store, "Burberry")
    assert store.set_override(sid, Sentiment.NEGATIVE, note="licence is core") == "Burberry"
    [override] = store.overrides()
    assert (override.story_id, override.brand) == (sid, "Burberry")
    assert override.sentiment is Sentiment.NEGATIVE
    assert override.note == "licence is core"
    assert override.title == "Burberry weighs licence sale"


def test_set_override_twice_updates(store: Store) -> None:
    sid = saved_story(store, "Burberry")
    store.set_override(sid, Sentiment.NEGATIVE)
    store.set_override(sid, Sentiment.POSITIVE, note="changed my mind")
    [override] = store.overrides()
    assert override.sentiment is Sentiment.POSITIVE
    assert override.note == "changed my mind"


def test_override_persists_across_instances(tmp_path: Path) -> None:
    first = Store(tmp_path / "tracker.db")
    sid = saved_story(first, "Burberry")
    first.set_override(sid, Sentiment.NEGATIVE)
    first.close()
    second = Store(tmp_path / "tracker.db")
    assert [o.story_id for o in second.overrides()] == [sid]
    second.close()


def test_set_override_unknown_story_errors(store: Store) -> None:
    with pytest.raises(OverrideError, match="unknown story id 'nope'"):
        store.set_override("nope", Sentiment.NEGATIVE)


def test_set_override_unanalysed_story_errors(store: Store) -> None:
    sid = saved_story(store)
    with pytest.raises(OverrideError, match="has not been analysed yet"):
        store.set_override(sid, Sentiment.NEGATIVE)


def test_multi_brand_story_requires_brand(store: Store) -> None:
    sid = saved_story(store, "Nike", "Adidas", title="Nike and Adidas sign rival deals")
    with pytest.raises(OverrideError, match=r"several brands \(Adidas, Nike\)"):
        store.set_override(sid, Sentiment.NEGATIVE)
    assert store.set_override(sid, Sentiment.NEGATIVE, brand="nike") == "Nike"


def test_override_for_brand_not_in_story_errors(store: Store) -> None:
    sid = saved_story(store, "Burberry")
    with pytest.raises(OverrideError, match="no analysis for 'Prada'"):
        store.set_override(sid, Sentiment.NEGATIVE, brand="Prada")


def test_apply_overrides_beats_model_sentiment(store: Store) -> None:
    sid = saved_story(store, "Nike", "Adidas", title="Nike and Adidas sign rival deals")
    store.set_override(sid, Sentiment.NEGATIVE, brand="Nike", note="PM view")
    model = [analysis(sid, "Nike", Sentiment.POSITIVE), analysis(sid, "Adidas", Sentiment.POSITIVE)]

    nike, adidas = apply_overrides(model, store.overrides())

    assert (nike.sentiment, nike.sentiment_source, nike.pm_note) == (
        Sentiment.NEGATIVE,
        "pm",
        "PM view",
    )
    assert (adidas.sentiment, adidas.sentiment_source) == (Sentiment.POSITIVE, "model")
    assert model[0].sentiment is Sentiment.POSITIVE  # inputs are not mutated


def test_story_keeps_id_when_reclustered_next_day(store: Store) -> None:
    day1 = make_story("Burberry weighs licence sale", "Burberry weighs beauty licence sale")
    store.save_stories([day1], datetime(2026, 10, 30, 9, tzinfo=UTC))
    # Next day the earliest item has aged out, so clustering yields a different id.
    day2 = make_story("Burberry weighs beauty licence sale", "Burberry licence sale talks advance")
    assert day2.id != day1.id

    store.save_stories([day2], datetime(2026, 10, 31, 9, tzinfo=UTC))

    assert day2.id == day1.id


def test_previous_status(store: Store) -> None:
    sid = saved_story(store, "Burberry")  # saved as a rumour on DAY1
    store.save_analyses(
        [Analysis(sid, "Burberry", True, Status.CONFIRMED, Sentiment.NEGATIVE, 4, "why")], DAY2
    )
    assert store.previous_status(sid, "Burberry", before=DAY2) is Status.RUMOUR
    assert store.previous_status(sid, "Burberry", before=DAY1) is None
    assert store.previous_status(sid, "Prada", before=DAY2) is None
