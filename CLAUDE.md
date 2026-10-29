# Project guide

## Stack
- Python 3.11, managed with `uv` (never use pip directly; add deps with `uv add <pkg>`)
- Code lives in `src/demo/`, tests in `tests/`
- pytest for tests, ruff for lint + format

## Commands
- Test: `uv run pytest -q`
- Lint/format: `uv run ruff check --fix . && uv run ruff format .`

## Conventions
- Type hints on all public functions; small, focused functions; docstrings only where intent isn't obvious
- Prefer the standard library; ask before adding a dependency
- Every new behavior gets a test; cover edge cases (empty input, invalid input, boundaries)

## Working style
- Before non-trivial changes: restate the requirement, list assumptions, propose a short plan, then wait for OK
- Keep diffs small and incremental; run tests + lint after each change
- Don't over-engineer: no abstractions, config layers, or features that weren't asked for
- Summarize what changed and why at the end of each step
