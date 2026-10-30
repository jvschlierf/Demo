from datetime import UTC, date, datetime
from pathlib import Path

import anthropic
import pytest

from demo import cli
from demo.cli import main
from demo.models import Analysis, RawItem, Sentiment, Status, Story
from demo.store import Store


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "tracker.db"
    store = Store(path)
    item = RawItem(
        "Reuters",
        "news",
        "Burberry weighs licence sale",
        "https://a.test/1",
        datetime(2026, 10, 30, 7, tzinfo=UTC),
    )
    store.save_stories([Story("abc123", item.title, [item])], datetime.now(UTC))
    store.save_analyses(
        [Analysis("abc123", "Burberry", True, Status.RUMOUR, Sentiment.NEUTRAL, 3, "why")],
        date(2026, 10, 30),
    )
    store.close()
    return path


def test_set_and_list_sentiment(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--db", str(db), "sentiment", "set", "abc123", "negative", "--note", "core"])
    assert code == 0
    assert "abc123 (Burberry): sentiment set to negative" in capsys.readouterr().out

    assert main(["--db", str(db), "sentiment", "list"]) == 0
    out = capsys.readouterr().out
    assert "abc123" in out and "negative" in out and "Burberry weighs licence sale - core" in out


def test_list_with_no_overrides(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(db), "sentiment", "list"]) == 0
    assert capsys.readouterr().out == "No sentiment overrides.\n"


def test_set_unknown_story_exits_1(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(db), "sentiment", "set", "nope", "negative"]) == 1
    assert "error: unknown story id 'nope'" in capsys.readouterr().err


def test_set_invalid_sentiment_is_a_usage_error(db: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "sentiment", "set", "abc123", "bearish"])
    assert exc.value.code == 2


def test_run_without_credentials_exits_1(
    db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_credentials() -> None:
        raise anthropic.AnthropicError("Could not resolve authentication method")

    monkeypatch.setattr(cli.anthropic, "Anthropic", no_credentials)
    assert main(["--db", str(db), "run"]) == 1
    err = capsys.readouterr().err
    assert "cannot reach Claude" in err and "ant auth login" in err


def test_run_rejects_non_positive_hours(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(db), "run", "--hours", "0"]) == 1
    assert "--hours must be at least 1" in capsys.readouterr().err
