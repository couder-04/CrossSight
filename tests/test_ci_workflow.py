"""CI runs lint, types, tests, and the dashboard build."""

from pathlib import Path

WORKFLOW = Path(".github/workflows/ci.yml").read_text()


def test_ci_workflow_covers_lint_types_tests_and_dashboard() -> None:
    for job in ("lint:", "types:", "tests:", "dashboard:"):
        assert job in WORKFLOW
    assert "uv sync --frozen --all-packages" in WORKFLOW
    assert "uv run ruff check ." in WORKFLOW
    assert "uv run ruff format --check ." in WORKFLOW
    assert "uv run mypy packages services --ignore-missing-imports --python-version 3.11" in WORKFLOW
    assert "uv run pytest -q" in WORKFLOW
    assert "npx pnpm@9 install --frozen-lockfile" in WORKFLOW
    assert "npx pnpm@9 typecheck" in WORKFLOW
    assert "npx pnpm@9 build" in WORKFLOW
