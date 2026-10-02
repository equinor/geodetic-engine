#!/usr/bin/env bash
#
# Build the documentation and serve it on http://127.0.0.1:8000 for viewing in
# a browser. In the devcontainer, VS Code forwards the port to the host.
#
# The build is the same one CI runs: warnings are errors and every example page
# is executed, so a failing example stops here rather than serving stale pages.
#
# Usage:
#   scripts/build-docs.sh              build, then serve on port 8000
#   scripts/build-docs.sh --port 8080  serve on another port
#   scripts/build-docs.sh --no-serve   build only
#   scripts/build-docs.sh --clean      discard the previous build first
#   scripts/build-docs.sh --live       rebuild and reload the browser on save
#
# Stop the server with Ctrl+C.

set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly OUTPUT="${REPO_ROOT}/docs/_build/html"

# The image has no locales beyond C; a forwarded host LANG such as en_US.UTF-8
# makes sphinx-build fail on start.
export LC_ALL=C.UTF-8

port=8000
serve=true
clean=false
live=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            port="${2:?--port needs a value}"
            shift 2
            ;;
        --no-serve)
            serve=false
            shift
            ;;
        --clean)
            clean=true
            shift
            ;;
        --live)
            live=true
            shift
            ;;
        -h | --help)
            sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'error: unknown option %s (see --help)\n' "$1" >&2
            exit 2
            ;;
    esac
done

if ! command -v uv >/dev/null 2>&1; then
    printf 'error: uv not found on PATH\n' >&2
    exit 1
fi

cd "${REPO_ROOT}"

if $clean; then
    rm -rf -- "${REPO_ROOT}/docs/_build"
fi

if $live; then
    # sphinx-autobuild's --open-browser fires before its server listens, and a
    # forwarded port then hangs until the next rebuild; open once it answers.
    if [[ -n "${BROWSER:-}" ]]; then
        (
            until curl -s -o /dev/null "http://127.0.0.1:${port}/"; do
                kill -0 $$ 2>/dev/null || exit 0
                sleep 1
            done
            "${BROWSER}" "http://127.0.0.1:${port}/" >/dev/null 2>&1
        ) &
    fi
    # Notebook execution writes beside the output, under docs/; only the output
    # itself is ignored by default.
    exec uv run --extra docs sphinx-autobuild docs "${OUTPUT}" \
        --host 127.0.0.1 --port "${port}" \
        --ignore "${REPO_ROOT}/docs/_build"
fi

uv run --extra docs sphinx-build -W --keep-going -b html docs "${OUTPUT}"

if ! $serve; then
    printf '\nBuilt: %s/index.html\n' "${OUTPUT}"
    exit 0
fi

url="http://127.0.0.1:${port}/"
printf '\nServing %s\nPress Ctrl+C to stop.\n\n' "${url}"
if [[ -n "${BROWSER:-}" ]]; then
    ("${BROWSER}" "${url}" >/dev/null 2>&1 &)
fi
exec python3 -m http.server "${port}" --bind 127.0.0.1 --directory "${OUTPUT}"
