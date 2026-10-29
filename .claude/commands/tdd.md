---
description: Implement a feature test-first
---
Feature: $ARGUMENTS

1. Write focused failing tests in `tests/` (happy path + edge cases). Run `uv run pytest -q` and show they fail.
2. Implement the minimum code in `src/demo/` to make them pass.
3. Run `uv run pytest -q` and `uv run ruff check --fix . && uv run ruff format .` until both are clean.
4. Summarize the diff in a few bullets.
