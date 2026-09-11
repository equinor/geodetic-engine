#!/usr/bin/env bash
#
# Run the whole test suite, including the exhaustive tests/testdataset sweep
# that the default configuration leaves out.
#
# That sweep is what -o addopts="" below is for: pyproject.toml sets
# addopts = ["-m", "not dataset"], so an ordinary `pytest` run collects about
# 1,300 tests, and clearing it collects about 14,500. Expect minutes rather
# than seconds, which is why the sweep is opt-in rather than the default.
#
# Arguments are forwarded to pytest, so a single case can still be picked out:
#
# Usage:
#   scripts/run_all_tests.sh [pytest args]
#   scripts/run_all_tests.sh tests/geodesy -k helmert

set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PYTHON="${REPO_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
    printf 'error: virtual environment not found at %s\n' "${REPO_ROOT}/.venv" >&2
    printf 'run: uv sync --extra dev\n' >&2
    exit 1
fi

if ! "${PYTHON}" -c 'import pytest, xdist' >/dev/null 2>&1; then
    printf 'error: pytest or pytest-xdist is missing from the virtual environment\n' >&2
    printf 'run: uv sync --extra dev\n' >&2
    exit 1
fi

cd "${REPO_ROOT}"
exec "${PYTHON}" -m pytest -o addopts="" -n logical "$@"
