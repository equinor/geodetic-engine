#!/usr/bin/env bash
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
