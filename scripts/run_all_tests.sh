#!/usr/bin/env bash
#
# Run the whole test suite, including the exhaustive tests/testdataset sweep
# that the default configuration leaves out.
#
# That sweep is what -o addopts="" below is for: pyproject.toml sets
# addopts = ["-m", "not dataset"], so an ordinary `pytest` run collects about
# 2,000 tests, and clearing it collects about 15,500. Expect minutes rather
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
# When both suites run, each one's output is also written to a log file and a
# summary of both -- the failures pytest listed and its closing tally -- is
# printed at the very end, because the first suite's own summary has long
# scrolled out of reach by the time the second one finishes.
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

has_local_tests() {
    [[ -d "${LOCAL_TESTS}" ]] &&
        find "${LOCAL_TESTS}" -type f \( -name 'test_*.py' -o -name '*_test.py' \) -print -quit |
            grep -q .
}

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
    if has_local_tests; then
        exec "${PYTHON}" -m pytest -o addopts="" -n "${workers}" -vv "${LOCAL_TESTS}" "$@"
    fi
    printf 'error: local/tests not found or has no test files\n' >&2
    exit 1
fi

if [[ $# -gt 0 ]]; then
    exec "${PYTHON}" -m pytest -o addopts="" -n "${workers}" -vv "$@"
fi

# Run one suite, showing its output live and keeping a copy for the summary.
# pytest sees a pipe, not a terminal, so colour is asked for explicitly.
run_suite() {
    local log="$1"
    shift
    local status=0
    "${PYTHON}" -m pytest -o addopts="" -n "${workers}" -vv --color=yes "$@" 2>&1 |
        tee -- "${log}" || status=$?
    return "${status}"
}

# The failures pytest listed and its closing tally, from one suite's log.
summarize() {
    local label="$1" log="$2" status="$3"
    local plain failures count
    plain="$(sed -E 's/\x1b\[[0-9;]*[[:alpha:]]//g' -- "${log}")"
    printf '\n%s (exit status %s)\n' "${label}" "${status}"
    failures="$(printf '%s\n' "${plain}" |
        sed -n '/^=* short test summary info =*$/,$p' |
        grep -E '^(FAILED|ERROR) ' || true)"
    if [[ -n "${failures}" ]]; then
        printf '%s\n' "${failures}" | head -n 40
        count="$(printf '%s\n' "${failures}" | wc -l)"
        if ((count > 40)); then
            printf '... and %d more, see %s\n' "$((count - 40))" "${log}"
        fi
    fi
    printf '%s\n' "${plain}" | grep -E '^=+ .* in [0-9.]+s.* =+$' | tail -n 1 ||
        printf '(pytest printed no tally; see %s)\n' "${log}"
}

logs="$(mktemp -d -t geodetic-engine-tests.XXXXXX)"
readonly OFFICIAL_LOG="${logs}/tests.log"
readonly LOCAL_LOG="${logs}/local-tests.log"

official_status=0
run_suite "${OFFICIAL_LOG}" || official_status=$?
status="${official_status}"

local_ran=false
local_status=0
if has_local_tests; then
    echo
    echo "==> local/tests (In house regression data)"
    local_ran=true
    run_suite "${LOCAL_LOG}" "${LOCAL_TESTS}" || local_status=$?
    [[ "${status}" -eq 0 ]] && status="${local_status}"
elif [[ -d "${LOCAL_TESTS}" ]]; then
    echo "==> local/tests exists but has no test files; skipping"
else
    echo "==> local/tests not found; skipping"
fi

echo
echo "==> Summary"
summarize "tests/ (official suite)" "${OFFICIAL_LOG}" "${official_status}"
if $local_ran; then
    summarize "local/tests (in-house regression data)" "${LOCAL_LOG}" "${local_status}"
fi
printf '\nfull output: %s\n' "${logs}"

exit "${status}"
