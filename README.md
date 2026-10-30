# Demo

Setup: `uv sync` · Tests: `uv run pytest` · Lint: `uv run ruff check --fix . && uv run ruff format .`

Note: if this folder is synced by iCloud (e.g. on the Desktop), iCloud marks files in `.venv` as hidden and Python then ignores the project's install, so `uv run demo` fails with `No module named 'demo'`. Keep the environment in an iCloud-excluded folder with a symlink (uv's own Python is used so Homebrew upgrades can't break it):

```sh
rm -rf .venv .venv.nosync && uv venv --python-preference only-managed .venv.nosync && ln -s .venv.nosync .venv && uv sync
```

