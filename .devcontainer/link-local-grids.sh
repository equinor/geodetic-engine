#!/usr/bin/env bash
#
# Make the grid files dropped in local/grids/ usable by PROJ without touching
# PROJ's installed data directory.
#
# Nothing under /usr/local/share/proj is modified: not the grids and not
# proj.db. The installed database stays the stock build of PROJ, so the
# database fingerprint a TransformationResult reports in the devcontainer is
# the one a stock installation reports, a test that passes here passes on a
# stock installation too, and scripts/build-projdb.sh still finds exactly one
# database to start from on PROJ's search path.
#
# The grids are read in place: .devcontainer/devcontainer.json lists local/grids
# after the installed directory on PROJ_DATA (after, because pyproj reads
# proj.db from the first entry only). A grid is then found under its own
# filename. A grid an EPSG operation names by a legacy filename PROJ has no
# grid_alternatives row for (see scripts/patch-grid-alternatives.sh) is found
# only through a patched database, which is deliberately not the installed
# one: build/proj.db, written by scripts/build-projdb.sh, carries the patch, or
# --patched-copy below writes a patched copy of the stock database to
# local/proj-data/ for the developer to put first on PROJ_DATA by hand. Either
# way the choice is explicit and local, and local/ is gitignored.
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

if [[ ${#grid_files[@]} -eq 0 && -f "$OVERLAY_DB" ]]; then
    rm -f -- "$OVERLAY_DB"
    echo "local/grids/ is empty, removed the patched copy of proj.db"
fi

$patched_copy || exit 0

# The installed, stock database: the first entry of PROJ_DATA that holds one
# other than the copy itself, or PROJ's default location.
installed_db=""
IFS=':' read -r -a search_path <<<"${PROJ_DATA:-$INSTALLED_DIR}"
for entry in "${search_path[@]}"; do
    candidate="${entry}/proj.db"
    if [[ -f "$candidate" && "$candidate" != "$OVERLAY_DB" ]]; then
        installed_db="$candidate"
        break
    fi
done
if [[ -z "$installed_db" ]]; then
    echo "error: no installed proj.db found on PROJ_DATA" >&2
    exit 1
fi

mkdir -p "$OVERLAY"
cp -- "$installed_db" "$OVERLAY_DB"
chmod u+w "$OVERLAY_DB"
echo "copied $installed_db to local/proj-data/"
"${REPO_ROOT}/scripts/patch-grid-alternatives.sh" --db "$OVERLAY_DB"
echo "to use it: export PROJ_DATA=${OVERLAY}:$(dirname "$installed_db"):${LOCAL_GRIDS}"
echo "(with two databases on the search path, a build needs its base named:" \
    "export GEODETIC_ENGINE_BASE_PROJ_DB=$installed_db)"
