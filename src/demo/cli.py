import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anthropic

from demo.brands import load_brands
from demo.claude import MODEL
from demo.models import Sentiment
from demo.pipeline import build_report
from demo.report import render_html, render_markdown
from demo.store import DEFAULT_DB_PATH, OverrideError, Store


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    store = Store(args.db)
    try:
        return args.handler(store, args)
    except OverrideError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="demo", description="UK/EMEA brand news tracker")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite store path")
    commands = parser.add_subparsers(required=True)

    run_cmd = commands.add_parser("run", help="build today's report")
    run_cmd.add_argument("--hours", type=int, default=24, help="look-back window (default 24)")
    run_cmd.add_argument("--out", type=Path, default=Path("out"), help="report directory")
    run_cmd.add_argument("--no-web", action="store_true", help="skip the web search scout")
    run_cmd.set_defaults(handler=_run)

    sentiment = commands.add_parser("sentiment", help="view or override story sentiment")
    sentiment_commands = sentiment.add_subparsers(required=True)

    set_cmd = sentiment_commands.add_parser("set", help="override the sentiment for a story")
    set_cmd.add_argument("story_id")
    set_cmd.add_argument("sentiment", choices=[s.value for s in Sentiment])
    set_cmd.add_argument("--brand", help="required when the story covers several brands")
    set_cmd.add_argument("--note", help="why you disagree with the model")
    set_cmd.set_defaults(handler=_set_sentiment)

    list_cmd = sentiment_commands.add_parser("list", help="list sentiment overrides")
    list_cmd.set_defaults(handler=_list_sentiment)
    return parser


def _run(store: Store, args: argparse.Namespace) -> int:
    if args.hours < 1:
        print("error: --hours must be at least 1", file=sys.stderr)
        return 1
    try:
        client = anthropic.Anthropic()
        client.models.retrieve(MODEL)  # free call: fails fast on missing or bad credentials
    except anthropic.AnthropicError as e:
        print(
            f"error: cannot reach Claude ({e}). Run `ant auth login` or set ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(UTC)
    report = build_report(
        load_brands(),
        store,
        client,
        now=now,
        window=timedelta(hours=args.hours),
        web=not args.no_web,
        progress=lambda message: print(f"{message}...", file=sys.stderr),
    )
    out_dir = args.out / f"{now:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (out_dir / "report.html").write_text(render_html(report), encoding="utf-8")
    print(f"Report written to {out_dir / 'report.html'} and {out_dir / 'report.md'}")
    return 0


def _set_sentiment(store: Store, args: argparse.Namespace) -> int:
    sentiment = Sentiment(args.sentiment)
    brand = store.set_override(args.story_id, sentiment, args.brand, args.note)
    print(f"{args.story_id} ({brand}): sentiment set to {sentiment.value}")
    return 0


def _list_sentiment(store: Store, args: argparse.Namespace) -> int:
    overrides = store.overrides()
    if not overrides:
        print("No sentiment overrides.")
    for o in overrides:
        note = f" - {o.note}" if o.note else ""
        print(f"{o.story_id}  {o.brand:<14} {o.sentiment.value:<8} {o.title}{note}")
    return 0
