#!/usr/bin/env bash
#
# Symlink every grid file dropped in local_grids/ into PROJ's data directory,
# so PROJ finds it without a copy and without every developer repeating the
# manual symlink by hand. Safe to run repeatedly, and a no-op when
# local_grids/ is empty, which it is until someone drops a grid file in it.
#
# Run automatically by the devcontainer on every start (see devcontainer.json)
# so a grid file already in local_grids/ -- kept there because it is gitignored,
# never committed -- is linked in again after a container rebuild.
#
# Usage:
#   .devcontainer/link-local-grids.sh

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LOCAL_GRIDS="${REPO_ROOT}/local_grids"

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
    echo "local_grids/ is empty, nothing to link"
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
        # example a previous local_grids/ file of the same name).
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
