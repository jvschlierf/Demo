# UK/EMEA Luxury & Sportswear News Tracker

A morning brief for a Portfolio Manager: news, social chatter and emerging rumours about luxury and sportswear brands, with a sentiment (positive / neutral / negative for the firm in question) on every story. The PM can override any sentiment, and the override carries into later reports.

This is a mock-up of the product: it runs against live public sources and writes an HTML + Markdown report. Share prices and market data are deliberately left out.

## Quick start

```sh
uv sync
ant auth login              # or: export ANTHROPIC_API_KEY=...
uv run demo run             # writes out/YYYY-MM-DD/report.html and report.md
```

## Commands

| Command | What it does |
|---|---|
| `uv run demo run [--hours 24] [--out out] [--no-web]` | Build today's report from the last `--hours` of coverage. `--no-web` skips the web search for a cheaper run. |
| `uv run demo sentiment set <story-id> {positive,neutral,negative} [--brand B] [--note "..."]` | Override a story's sentiment. The story ID is shown next to each story in the report. `--brand` is needed when a story covers several brands. |
| `uv run demo sentiment list` | List your overrides. |

All commands take `--db PATH` (default `data/tracker.db`).

**Rough cost per run:** about $1–2 with `--no-web`, and about $3–9 with web search on for all 30 brands.

## How it works

```
news feeds + Reddit ─┐
                     ├─► merge duplicates ─► screen ─► analyse ─► apply PM overrides ─► summarise ─► report
web search (Claude) ─┘
```

1. **Collect**:
   - Google News (UK edition) for each brand and its aliases.
   - Trade press: Guardian Business, Business of Fashion, FashionUnited UK, Drapers.
   - Reddit: r/Sneakers, r/fashion, r/handbags, r/UKInvesting.
   - Market-data sites and stock-price headlines are dropped here.
2. **Web search**: one Claude call per brand searches the web for UK/EMEA news and rumours. Only URLs that appear in real search results are used, and pages older than the window are dropped.
3. **Merge duplicates**: items about the same event for the same brand become one story.
4. **Screen**: Claude keeps business news and rumours, and drops noise (deals, release dates, celebrity pieces, namesakes). It also groups headlines about the same event.
5. **Analyse**: for each story and brand, Claude gives:
   - UK/EMEA relevance
   - status: rumour, confirmed or denied
   - sentiment
   - materiality, 1–5
   - a one-line reason
6. **Store**: stories, analyses and overrides are kept in SQLite. A story keeps its ID across days, so overrides persist and the report can flag a status change (e.g. "was rumour").
7. **Report**, in this order:
   - executive summary
   - **Rumour watch**
   - per-sector, per-brand sections, with each story's sources linked inline

Every Claude call uses `claude-opus-5-5`. The screen and the analysis run at low effort, and the summary at medium.

## Brands

There are 30 brands in [src/demo/brands.toml](src/demo/brands.toml), 15 luxury and 15 sportswear. To track a new brand, add an entry; no code changes are needed:

```toml
[[brand]]
name = "Burberry"
sector = "luxury"          # or "sportswear"
ticker = "BRBY.L"          # optional
aliases = ["Burberry Group"]  # sub-brands and key people; also used as search terms
# scout = false            # skip the daily web search for this brand
```

Use specific aliases (e.g. "Coach handbag" rather than "Coach"), because aliases are matched as whole words and also used as Google News search terms.

## Layout

| Path | Purpose |
|---|---|
| `src/demo/sources.py` | RSS/Atom feeds, Reddit, filtering |
| `src/demo/scout.py` | Claude web search per brand |
| `src/demo/dedupe.py` | Story IDs and duplicate merging |
| `src/demo/analyse.py` | Claude screen and per-story analysis |
| `src/demo/summarise.py` | Executive summary |
| `src/demo/store.py` | SQLite store and sentiment overrides |
| `src/demo/report.py` | Markdown and HTML rendering |
| `src/demo/pipeline.py` | Runs the steps end to end |
| `src/demo/cli.py` | `demo` command |

## Development

- Tests: `uv run pytest -q`. Tests use no network and a fake Claude client.
- Lint and format: `uv run ruff check --fix . && uv run ruff format .`

### iCloud note

If this folder is synced by iCloud (e.g. on the Desktop), iCloud hides the files in `.venv` and Python then skips the project's install. `uv run demo` then fails with `No module named 'demo'`.

The fix is to keep the environment in a folder iCloud excludes and link to it. This uses uv's own Python, so Homebrew upgrades can't break it:

```sh
rm -rf .venv .venv.nosync && uv venv --python-preference only-managed .venv.nosync && ln -s .venv.nosync .venv && uv sync
```
