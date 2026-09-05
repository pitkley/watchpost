# Repository Guidelines

## Project Structure & Module Organization

Watchpost is a Python 3.13+ framework for code-driven Checkmk monitoring. Core code lives in `src/watchpost/`, with modules for checks, results, execution, scheduling, datasources, caching, and ASGI/HTTP handling. The CLI is in `src/watchpost/cli/`; third-party code is in `src/watchpost/vendored/`. Tests live in `tests/`. Use `examples/basic/` for a runnable application, `docs/` for MkDocs documentation, and `checkmk-integration/` for agent scripts, the Checkmk plugin, and Docker packaging. Maintenance scripts live in `.tools/`.

## Build, Test, and Development Commands

Run commands from the repository root unless specified:

- `uv sync --all-extras --all-groups`: install runtime, optional, development, and documentation dependencies.
- `uv run lefthook install`: enable pre-commit and pre-push checks.
- `uv build`: build distribution packages in `dist/`.
- `uv run pytest -m 'not docker'`: run tests without Docker dependencies.
- `uv run pytest`: run the full suite, including Docker-backed Redis tests.
- `uv run ruff check .` and `uv run ruff format --check .`: check lint and formatting; use `uv run ruff format .` to format.
- `uv run ty check` and `uv run mypy examples/ src/`: run type checks.
- `uv run --all-extras --all-groups mkdocs build --strict`: validate and build documentation.

From `examples/basic/`, run `uv run watchpost --app basic:app run-checks` to exercise the example locally.

## Coding Style & Naming Conventions

Use four-space indentation, Ruff formatting and import sorting, `snake_case` for functions/modules, `PascalCase` for classes, and `UPPER_CASE` for constants. Annotate function parameters and return types; mypy rejects untyped definitions. Preserve copyright and SPDX headers in Python and shell files, including original notices in vendored code.

## Testing Guidelines

Use pytest with files named `test_*.py` and functions named `test_*`. Add regression tests alongside the affected feature, covering synchronous and asynchronous behavior where applicable. Mark Docker-dependent tests with `@pytest.mark.docker`. No numerical coverage threshold is configured; CI runs the full suite.

## Commit & Pull Request Guidelines

History uses concise imperative subjects and dependency prefixes such as `chore(deps):` and `fix(deps):`. Follow those patterns. PRs should explain the behavior change, link relevant issues, list validation performed, and update examples or documentation for public API changes. Keep `uv.lock` synchronized with dependency changes and regenerate `THIRD_PARTY_LICENSES.md` using `.tools/show-third-party-licenses.sh` when needed. Ensure CI passes before merging.
