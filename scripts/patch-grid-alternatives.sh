#!/usr/bin/env bash
#
# Patch a built proj.db's grid_alternatives table with filename mappings that
# are known to be missing upstream.
#
# An authority's coordinate operation names a grid by its own filename
# (grid_transformation.grid_name / other_transformation.grid_name).
# grid_alternatives is PROJ's own mapping from that name to the file its
# tooling and CDN actually ship under -- and occasionally that mapping is
# missing even though both the referencing operation and the grid file itself
# are fine. Left unpatched, PROJ reports the grid as missing, indistinguishable
# from the grid genuinely being unavailable.
#
# This is deliberately a separate, opt-in step rather than something the
# projdb build does automatically: a proj.db built without running this script
# is exactly the official database plus this package's own authority data, no
# more. Each patch below is scoped to one authority's own grid name and is
# removed the day PROJ ships the mapping upstream (each entry names why it
# still exists).
#
# Usage:
#   scripts/patch-grid-alternatives.sh [-d|--db PATH]
#
# Run with --help for details.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly INVOCATION_DIR="$PWD"

db="build/proj.db"
db_given=false

usage() {
    cat <<'EOF'
Patch a built proj.db's grid_alternatives table with filename mappings known
to be missing upstream, so a grid PROJ cannot find under the authority's own
name is found under the name PROJ's own tooling/CDN ships it as.

Options:
  -d, --db PATH   Database to patch. Default: build/proj.db.
  -h, --help      Show this message.

Each patch is idempotent and forward compatible: a grid name already present
in grid_alternatives (because a newer PROJ release shipped the fix, or this
script already ran) is left untouched rather than replaced. A backup is taken
before patching and removed only if every patch applies cleanly.

Example:
  scripts/patch-grid-alternatives.sh --db build/proj.db
EOF
}

die() {
    echo "error: $*" >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -d | --db)
            db="${2:-}"
            db_given=true
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "unknown option $1 (try --help)"
            ;;
    esac
done

command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 is required but not on PATH"

absolute() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s\n' "${INVOCATION_DIR}/$1" ;;
    esac
}

if $db_given; then
    db="$(absolute "$db")"
else
    db="${REPO_ROOT}/${db}"
fi

[[ -f "$db" ]] || die "proj.db not found at $db (build one first, or pass --db)"

echo "patching grid_alternatives in $db"
backup="${db}.grid-alternatives.bak"
cp -- "$db" "$backup"

# Each row: original_grid_name | proj_grid_name | proj_grid_format |
#           proj_method | url (or "" if none) | reason
#
# Verified missing against proj.db built from PROJ 9.8.1 / EPSG v12.029.
# Re-check with:
#   sqlite3 "$db" "SELECT * FROM grid_alternatives WHERE original_grid_name='<name>';"
# before removing an entry once PROJ ships it upstream.
patches=(
    "Und_min1x1_egm2008_isw=82_WGS84_TideFree|us_nga_egm2008_1.tif|GTiff|geoid_like||1' EGM2008 geoid used by EPSG:3859/9618 (WGS 84 to EGM2008 height); only the 2.5' variant (us_nga_egm08_25.tif) has a grid_alternatives row upstream"
    "Und_min1x1_egm2008_isw=82_WGS84_TideFree.gz|us_nga_egm2008_1.tif|GTiff|geoid_like||Same grid as above, referenced under its .gz name by EPSG:8037/9706"
    "NNTrans2018B.gtx|no_kv_HREF2018B_NN54_NN2000.tif|GTiff|vgridshift|https://cdn.proj.org/no_kv_HREF2018B_NN54_NN2000.tif|NN54 height to NN2000 height (EPSG:11156); PROJ's CDN ships the .tif, not the legacy .gtx the operation names"
)

patched=0
skipped=0
failed=0

for entry in "${patches[@]}"; do
    IFS='|' read -r original proj_grid format method url reason <<<"$entry"

    already="$(sqlite3 "$db" \
        "SELECT count(*) FROM grid_alternatives WHERE original_grid_name = '${original//\'/\'\'}';")"
    if [[ "$already" != "0" ]]; then
        echo "  skip:  $original (already mapped)"
        skipped=$((skipped + 1))
        continue
    fi

    referenced="$(sqlite3 "$db" "
        SELECT count(*) FROM grid_transformation WHERE grid_name = '${original//\'/\'\'}'
        UNION ALL
        SELECT count(*) FROM other_transformation WHERE grid_name = '${original//\'/\'\'}';" \
        | awk '{s+=$1} END {print s+0}')"
    if [[ "$referenced" == "0" ]]; then
        echo "  warn:  $original is not referenced by any operation in this database, skipping" >&2
        echo "         ($reason)" >&2
        failed=$((failed + 1))
        continue
    fi

    if [[ -n "$url" ]]; then
        sqlite3 "$db" "
            INSERT INTO grid_alternatives (
                original_grid_name, proj_grid_name, proj_grid_format, proj_method,
                inverse_direction, url, direct_download, open_license
            ) VALUES (
                '${original//\'/\'\'}', '${proj_grid//\'/\'\'}', '${format//\'/\'\'}',
                '${method//\'/\'\'}', 0, '${url//\'/\'\'}', 1, 1
            );"
    else
        sqlite3 "$db" "
            INSERT INTO grid_alternatives (
                original_grid_name, proj_grid_name, proj_grid_format, proj_method,
                inverse_direction
            ) VALUES (
                '${original//\'/\'\'}', '${proj_grid//\'/\'\'}', '${format//\'/\'\'}',
                '${method//\'/\'\'}', 0
            );"
    fi
    echo "  added: $original -> $proj_grid ($reason)"
    patched=$((patched + 1))
done

if [[ "$failed" -gt 0 ]]; then
    echo "restoring backup because $failed patch(es) could not be verified" >&2
    cp -- "$backup" "$db"
    rm -f -- "$backup"
    die "$failed grid_alternatives patch(es) failed; database left unchanged"
fi

rm -f -- "$backup"
echo "done: $patched patched, $skipped already present"
