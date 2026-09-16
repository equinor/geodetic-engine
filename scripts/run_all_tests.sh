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
# When run with no arguments, local/tests is also run if it exists and holds
# test files. That directory (like the rest of local/) is gitignored: it is
# for local internal regression data and the tests that read it, shared
# outside of git, so most checkouts will not have it. It runs as a separate
# pytest invocation because it is a directory named "tests" too, and pytest
# cannot collect two same-named directories in one run without a module name
# clash. Passing arguments narrows the run to the main suite only, as before,
# since at that point the caller is picking something specific out of tests/.
#
# Arguments are forwarded to pytest, so a single case can still be picked out:
#
# Usage:
#   scripts/run_all_tests.sh [pytest args]
#   scripts/run_all_tests.sh tests/geodesy -k helmert
#   scripts/run_all_tests.sh --local-only [pytest args]
#   scripts/run_all_tests.sh --workers 4 [pytest args]
#
# -n logical (the default) spawns one worker per logical CPU the container
# sees, which is Docker Desktop's CPU setting, not necessarily your host's.
# --workers overrides it, for example --workers 4 for a lighter interactive
# run; it must come before any pytest args, same as --local-only.

set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PYTHON="${REPO_ROOT}/.venv/bin/python"
readonly LOCAL_TESTS="${REPO_ROOT}/local/tests"

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

readonly WORKERS_DEFAULT="${PYTEST_WORKERS:-logical}"
workers="${WORKERS_DEFAULT}"
local_only=false

cd "${REPO_ROOT}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local-only)
            local_only=true
            shift
            ;;
        --workers)
            workers="${2:?--workers needs a value}"
            shift 2
            ;;
        *)
            break
            ;;
    esac
done

if $local_only; then
    if [[ -d "${LOCAL_TESTS}" ]] &&
        find "${LOCAL_TESTS}" -type f \( -name 'test_*.py' -o -name '*_test.py' \) -print -quit |
            grep -q .; then
        exec "${PYTHON}" -m pytest -o addopts="" -n "${workers}" -vv "${LOCAL_TESTS}" "$@"
    fi
    printf 'error: local/tests not found or has no test files\n' >&2
    exit 1
fi

if [[ $# -gt 0 ]]; then
    exec "${PYTHON}" -m pytest -o addopts="" -n "${workers}" -vv "$@"
fi

status=0
"${PYTHON}" -m pytest -o addopts="" -n "${workers}" -vv || status=$?

if [[ -d "${LOCAL_TESTS}" ]] &&
    find "${LOCAL_TESTS}" -type f \( -name 'test_*.py' -o -name '*_test.py' \) -print -quit |
        grep -q .; then
    echo
    echo "==> local/tests (In house regression data)"
    local_status=0
    "${PYTHON}" -m pytest -o addopts="" -n "${workers}" -vv "${LOCAL_TESTS}" || local_status=$?
    [[ "${status}" -eq 0 ]] && status="${local_status}"
elif [[ -d "${LOCAL_TESTS}" ]]; then
    echo "==> local/tests exists but has no test files; skipping"
else
    echo "==> local/tests not found; skipping"
fi

exit "${status}"
