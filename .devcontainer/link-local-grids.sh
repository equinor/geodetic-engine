#!/usr/bin/env bash
#
# Make the grid files dropped in local/grids/ usable by PROJ without touching
# PROJ's installed data directory.
#
# Nothing under /usr/local/share/proj is modified: not the grids and not
# proj.db. The installed database stays the stock build of PROJ, so a test
# that passes against it passes on a stock installation too, and
# scripts/build-projdb.sh can always start from it.
#
# The grids are read in place: .devcontainer/devcontainer.json lists local/grids
# last on PROJ_DATA. A grid is then found under its own filename. A grid an
# EPSG operation names by a legacy filename PROJ has no grid_alternatives row
# for (see scripts/patch-grid-alternatives.sh) is found only through a patched
# database, which is deliberately not the installed one.
#
# The database PROJ reads is local/proj-data/proj.db, first on PROJ_DATA. By
# default it is a link that scripts/install-osdu-projdb.sh manages: to
# build/proj.db, which carries the OSDU catalogue and the grid patch, or to the
# stock database when the catalogue could not be installed. --patched-copy
# below replaces it with a patched copy of the stock database instead. Either
# way the choice is local, and local/ is gitignored.
#
# Run by the devcontainer on every start to create local/grids/ and report
# what is in it. Safe to run repeatedly.
#
# Usage:
#   .devcontainer/link-local-grids.sh [--check] [--patched-copy]
#
# --check         Only report whether local/grids/ is on PROJ's search path,
#                 for scripts/build-projdb.sh to warn when a grid there would
#                 not be found.
# --patched-copy  Also write local/proj-data/proj.db, a copy of the installed
#                 database with scripts/patch-grid-alternatives.sh applied.

set -euo pipefail

check_only=false
patched_copy=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) check_only=true; shift ;;
        --patched-copy) patched_copy=true; shift ;;
        # An earlier version of this script linked grids into the installed
        # directory and patched the installed database; nothing is linked or
        # patched in place now, so the old flag only reports.
        --links-only) check_only=true; shift ;;
        *) echo "error: unknown option $1" >&2; exit 2 ;;
    esac
done

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LOCAL_GRIDS="${REPO_ROOT}/local/grids"
readonly OVERLAY="${REPO_ROOT}/local/proj-data"
readonly OVERLAY_DB="${OVERLAY}/proj.db"
readonly INSTALLED_DIR="/usr/local/share/proj"

mkdir -p "$LOCAL_GRIDS"

on_search_path() {
    local IFS=':' entry
    for entry in ${PROJ_DATA:-}; do
        [[ "$entry" == "$1" ]] && return 0
    done
    return 1
}

shopt -s nullglob
grid_files=("$LOCAL_GRIDS"/*)

if on_search_path "$LOCAL_GRIDS"; then
    echo "local/grids/ is on PROJ_DATA (${#grid_files[@]} file(s))"
elif [[ ${#grid_files[@]} -gt 0 ]]; then
    echo "warn: local/grids/ holds ${#grid_files[@]} file(s) but is not on PROJ_DATA;" \
        "grids there will not be found. Export" \
        "PROJ_DATA=${INSTALLED_DIR}:${LOCAL_GRIDS}" >&2
else
    echo "local/grids/ is empty and not on PROJ_DATA"
fi

$check_only && exit 0

$patched_copy || exit 0

# The installed, stock database: the first entry of PROJ_DATA that holds one,
# other than the copy's own directory, or PROJ's default location.
installed_db=""
IFS=':' read -r -a search_path <<<"${PROJ_DATA:-$INSTALLED_DIR}"
for entry in "${search_path[@]}"; do
    candidate="${entry}/proj.db"
    # The directory is compared, not the file: local/proj-data/proj.db may be a
    # link to the stock database itself, which must not then be skipped. -ef
    # compares the directory however PROJ_DATA spells or links its path.
    if [[ -f "$candidate" ]] && ! [[ -d "$OVERLAY" && "$entry" -ef "$OVERLAY" ]]; then
        installed_db="$candidate"
        break
    fi
done
if [[ -z "$installed_db" ]]; then
    echo "error: no installed proj.db found on PROJ_DATA" >&2
    exit 1
fi

if [[ -L "$OVERLAY" ]]; then
    echo "error: $OVERLAY is a symlink; refusing to write the patched copy" \
        "through it" >&2
    exit 1
fi
mkdir -p "$OVERLAY"
if [[ "$OVERLAY" -ef "$INSTALLED_DIR" ]]; then
    echo "error: $OVERLAY resolves to PROJ's installed data directory;" \
        "refusing to replace its proj.db" >&2
    exit 1
fi
# Renamed into place, so a failure keeps the copy in use and a link is replaced.
staged="$(mktemp "${OVERLAY}/proj.db.XXXXXX")"
trap 'rm -f -- "$staged"' EXIT
cp -- "$installed_db" "$staged"
"${REPO_ROOT}/scripts/patch-grid-alternatives.sh" --db "$staged"
mv -fT -- "$staged" "$OVERLAY_DB"
echo "wrote a patched copy of $installed_db to local/proj-data/"
echo "to use it: export PROJ_DATA=${OVERLAY}:$(dirname "$installed_db"):${LOCAL_GRIDS}"
echo "(with two databases on the search path, a build needs its base named:" \
    "export GEODETIC_ENGINE_BASE_PROJ_DB=$installed_db)"
