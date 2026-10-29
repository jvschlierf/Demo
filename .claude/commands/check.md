---
description: Run lint and tests, fix any failures
---
Run `uv run ruff check --fix . && uv run ruff format .` then `uv run pytest -q`.
If anything fails, fix the root cause (don't weaken tests), re-run, and summarize what you fixed.
