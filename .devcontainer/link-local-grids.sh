#!/usr/bin/env bash
#
# Symlink every grid file dropped in local/grids/ into PROJ's data directory,
# so PROJ finds it without a copy and without every developer repeating the
# manual symlink by hand. Safe to run repeatedly, and a no-op when
# local/grids/ is empty, which it is until someone drops a grid file in it.
#
# A grid linked in under its own filename still needs to be found under the
# name the operation that uses it declares, which is often different (the
# EPSG-registered name vs. the file PROJ's CDN ships); scripts/patch-grid-
# alternatives.sh carries the known mappings for that, so it is run here too,
# against the installed proj.db rather than a built one.
#
# Run automatically by the devcontainer on every start (see devcontainer.json)
# so a grid file already in local/grids/ -- kept there because it is gitignored,
# never committed -- is linked in again after a container rebuild.
#
# Usage:
#   .devcontainer/link-local-grids.sh [--links-only]
#
# --links-only updates file links without patching the installed database.
# Used by build-projdb.sh, which patches its own staged database separately.

set -euo pipefail

links_only=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --links-only) links_only=true; shift ;;
        *) echo "error: unknown option $1" >&2; exit 2 ;;
    esac
done

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LOCAL_GRIDS="${REPO_ROOT}/local/grids"

mkdir -p "$LOCAL_GRIDS"

# PROJ_DATA can be a colon-separated search path; the first entry is where
# PROJ looks first, and where the Dockerfile points it by default.
target_dir="${PROJ_DATA:-/usr/local/share/proj}"
target_dir="${target_dir%%:*}"

if [[ ! -d "$target_dir" ]]; then
    echo "warn: PROJ data directory $target_dir does not exist, skipping" >&2
    exit 0
fi

shopt -s nullglob
grid_files=("$LOCAL_GRIDS"/*)
if [[ ${#grid_files[@]} -eq 0 ]]; then
    echo "local/grids/ is empty, nothing to link"
    exit 0
fi

for src in "${grid_files[@]}"; do
    [[ -f "$src" ]] || continue
    name="$(basename "$src")"
    dest="${target_dir}/${name}"

    if [[ -L "$dest" ]]; then
        if [[ "$(readlink -f "$dest")" == "$(readlink -f "$src")" ]]; then
            echo "already linked: $name"
            continue
        fi
        # A symlink under our control, just pointed at something else (for
        # example a previous local/grids/ file of the same name).
    elif [[ -e "$dest" ]]; then
        echo "warn: $dest already exists and is not a symlink, leaving it alone" >&2
        continue
    fi

    if [[ -w "$target_dir" ]]; then
        ln -sf "$src" "$dest"
    elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo -n ln -sf "$src" "$dest"
    else
        echo "warn: $target_dir is not writable and passwordless sudo is not" \
            "available, skipping $name" >&2
        continue
    fi
    echo "linked: $name -> $target_dir/"
done

$links_only && exit 0

patch_script="${REPO_ROOT}/scripts/patch-grid-alternatives.sh"
target_db="${target_dir}/proj.db"
if [[ ! -f "$target_db" ]]; then
    echo "warn: $target_db not found, skipping grid_alternatives patch" >&2
elif [[ -w "$target_db" ]]; then
    "$patch_script" --db "$target_db" ||
        echo "warn: grid_alternatives patch failed; linked grids may still report as missing" >&2
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo -n "$patch_script" --db "$target_db" ||
        echo "warn: grid_alternatives patch failed; linked grids may still report as missing" >&2
else
    echo "warn: $target_db is not writable and passwordless sudo is not" \
        "available, skipping grid_alternatives patch" >&2
fi
