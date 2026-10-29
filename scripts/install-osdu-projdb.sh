#!/usr/bin/env bash
#
# Make the official OSDU catalogue part of the proj.db PROJ reads, without
# touching PROJ's installed database.
#
# The CRS_CT.json catalogue OSDU publishes is committed in catalogs/osdu/, byte
# for byte, with where it came from recorded beside it. This builds it into
# build/proj.db with scripts/build-projdb.sh and points local/proj-data/proj.db
# at the result. The devcontainer lists local/proj-data first on PROJ_DATA, so
# the OSDU CRSs, bound CRSs, transformations, record ids and persistable
# references resolve out of the box. Nothing is downloaded unless --update is
# given: that first replaces the committed catalogue with the newest OSDU
# publishes (geodetic-osdudb download), for the change to be reviewed and
# committed.
#
# A failure never fails the caller. The devcontainer is still created and a
# warning says what went wrong. A build is published only once it succeeds, so
# an earlier build/proj.db made for the installed PROJ stays in use, with the
# OSDU catalogue it holds. Without one, the warning says no OSDU objects are
# available and local/proj-data/proj.db points at the stock database instead,
# so PROJ works exactly as installed. A failed --update warns and builds the
# committed catalogue. --strict exits non-zero on a failure instead.
#
# build/proj.db is reused when it already holds the committed catalogue, so a
# rebuilt container builds nothing. A catalogue changed by --update, or by a
# pull that brought a newer one, is imported again, as is any with --force. A
# database that also holds another source's import, such as the Georepository
# register's, is added to and never replaced, unless it was built for another
# PROJ release or database layout than the installed one, after a PROJ upgrade
# say. PROJ cannot open a database of another layout at all, and one of another
# release holds that release's EPSG dataset, so it is never linked: it is moved
# to build/proj.db.stale and the official catalogue is built from scratch.
# Whatever another source had imported into it has to be built again.
#
# Catalogues of your own are built on top, after the official one, every time:
# each *.json in local/catalogs/custom/ in name order, then each --custom. Each
# is appended with --overwrite-rows, importing every code space it publishes
# records under but EPSG, so its own CRSs and transformations are added, and a
# record it states differently from the official catalogue replaces the
# official definition. Every replacement is listed as REPLACED in that build's
# summary and its report. A record stated exactly as the official one leaves it
# unchanged, so applying the same catalogue again changes nothing. Objects of
# PROJ's own EPSG dataset are never replaced.
#
# local/proj-data/proj.db is always a link this script manages, except when it
# is a file written by .devcontainer/link-local-grids.sh --patched-copy: that is
# a developer's choice and is left in use, with a note.
#
# Usage:
#   scripts/install-osdu-projdb.sh [--update [--ref REF] | --catalog PATH]
#                                  [--custom PATH]... [--force] [--strict]
#   scripts/install-osdu-projdb.sh --check
#
# Run with --help for the options.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly INVOCATION_DIR="$PWD"
readonly ACTIVE_DIR="${REPO_ROOT}/local/proj-data"
readonly ACTIVE_DB="${ACTIVE_DIR}/proj.db"
readonly BUILT_DB="${REPO_ROOT}/build/proj.db"
readonly STALE_DB="${BUILT_DB}.stale"
# Relative, so the link survives the repository being mounted elsewhere.
readonly BUILT_DB_LINK="../../build/proj.db"
readonly DEFAULT_CATALOG="${REPO_ROOT}/catalogs/osdu/CRS_CT_osdu_official_crs_catalog.json"
readonly CUSTOM_DIR="${REPO_ROOT}/local/catalogs/custom"
readonly INSTALLED_DB="/usr/local/share/proj/proj.db"
# The source_version a build of the official catalogue records.
readonly OFFICIAL_SOURCE="osdu/data/data-definitions"

update=false
ref=""
catalog=""
custom_args=()
force=false
strict=false
check_only=false

usage() {
    cat <<'EOF'
Build the official OSDU catalogue committed in catalogs/osdu/ into the proj.db
PROJ reads, with any catalogues of your own on top.

Options:
      --update        First replace the committed catalogue with the newest
                      OSDU publishes, downloading it. Review and commit the
                      change to catalogs/osdu/.
      --ref REF       With --update: the revision to fetch instead of the
                      newest.
      --catalog PATH  Build this copy of the official catalogue instead of the
                      committed one.
      --custom PATH   A catalogue of your own, built on top of the official
                      one: its CRSs and transformations are added, and a record
                      it states differently replaces the official definition.
                      Repeatable. Every *.json in local/catalogs/custom/ is
                      built on top too, first, in name order.
      --force         Import the official catalogue again even if
                      build/proj.db already holds it.
      --strict        Exit non-zero when a catalogue could not be installed or
                      updated. By default a failure only warns.
      --check         Build nothing: point local/proj-data/proj.db at
                      build/proj.db, or at the stock database when there is
                      none built for the installed PROJ, and report whether
                      the OSDU catalogue is in the database PROJ reads.
  -h, --help          Show this message.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --update) update=true; shift ;;
        --ref) ref="${2:-}"; update=true; shift 2 ;;
        --catalog) catalog="${2:-}"; shift 2 ;;
        --custom) custom_args+=("${2:-}"); shift 2 ;;
        --force) force=true; shift ;;
        --strict) strict=true; shift ;;
        --check) check_only=true; shift ;;
        -h | --help) usage; exit 0 ;;
        *) echo "error: unknown option $1 (try --help)" >&2; exit 2 ;;
    esac
done

absolute() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s\n' "${INVOCATION_DIR}/$1" ;;
    esac
}

[[ -n "$catalog" ]] && catalog="$(absolute "$catalog")"
if $update && [[ -n "$catalog" ]]; then
    echo "error: --update replaces the committed catalogue; it cannot be" \
        "combined with --catalog" >&2
    exit 2
fi
customs=()
shopt -s nullglob
for file in "$CUSTOM_DIR"/*.json; do
    customs+=("$file")
done
shopt -u nullglob
for file in ${custom_args[@]+"${custom_args[@]}"}; do
    customs+=("$(absolute "$file")")
done

if [[ -n "${GEODETIC_RUNNER:-}" ]]; then
    read -r -a runner <<<"$GEODETIC_RUNNER"
elif command -v uv >/dev/null 2>&1 && [[ -f "${REPO_ROOT}/pyproject.toml" ]]; then
    runner=(uv run --project "$REPO_ROOT")
else
    runner=()
fi

# One value from a read-only query, or nothing when the database or table is
# missing.
query() {
    local database="$1" sql="$2"
    [[ -f "$database" ]] || return 0
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 -readonly "$database" "$sql" 2>/dev/null || true
    else
        python3 - "$database" "$sql" 2>/dev/null <<'EOF' || true
import sqlite3
import sys
from contextlib import closing

# As geodetic_engine._sqlite.read_only(): "%", "?" and "#" are URI syntax,
# and a leading "//" would be read as the URI's authority.
path = sys.argv[1].translate(str.maketrans({"%": "%25", "?": "%3F", "#": "%23"}))
uri = f"file:{'//' if path.startswith('//') else ''}{path}?mode=ro"
with closing(sqlite3.connect(uri, uri=True)) as db:
    row = db.execute(sys.argv[2]).fetchone()
    print(row[0] if row else "")
EOF
    fi
}

osdu_ids() {
    local count
    count="$(query "$1" "SELECT count(*) FROM geodetic_engine_osdu_object_identifier WHERE scheme = 'OSDU'")"
    echo "${count:-0}"
}

osdu_references() {
    local count
    count="$(query "$1" "SELECT count(*) FROM geodetic_engine_osdu_persistable_reference WHERE scheme = 'OSDU'")"
    echo "${count:-0}"
}

built_here() {
    [[ "$(query "$1" "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'geodetic_engine_build_history'")" == 1 ]]
}

# What a build of an official catalogue records as its source_version: the
# commit it was published at, as recorded beside it, or else its SHA-256.
version_of() {
    local catalog="$1" provenance="${1%.json}.source.json" sha commit=""
    sha="$(sha256sum "$catalog" | cut -d' ' -f1)"
    if [[ -f "$provenance" ]] && grep -q "\"sha256\": \"${sha}\"" "$provenance"; then
        commit="$(sed -n 's/^ *"commit": "\([0-9a-f]*\)".*/\1/p' "$provenance")"
    fi
    echo "${OFFICIAL_SOURCE}@${commit:-sha256:${sha}}"
}

# Whether the latest build of an official catalogue into a database was of
# exactly this one. An earlier build of it does not count: a later official
# catalogue has replaced what it wrote. Custom catalogues record no
# source_version of this form and are passed over.
holds_version() {
    [[ "$(query "$1" "SELECT count(*) FROM (SELECT report FROM geodetic_engine_build_history WHERE report LIKE '%\"source_version\": \"${OFFICIAL_SOURCE}@%' ORDER BY sequence DESC LIMIT 1) WHERE report LIKE '%\"source_version\": \"$2\"%'")" -gt 0 ]]
}

# The code spaces a catalogue publishes its own records under, one per line:
# every one but EPSG, whose objects PROJ's database already defines.
code_spaces() {
    python3 - "$1" <<'EOF'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    records = json.load(stream).get("ReferenceData", [])
spaces = {record.get("data", {}).get("CodeSpace") for record in records}
print("\n".join(sorted(space for space in spaces if space and space != "EPSG")))
EOF
}

# The database PROJ was installed with: the first on PROJ_DATA, other than
# local/proj-data, that this package did not build.
stock_db() {
    local IFS=':' entry
    for entry in ${PROJ_DATA:-}; do
        [[ -n "$entry" && -f "${entry}/proj.db" ]] || continue
        [[ -d "$ACTIVE_DIR" && "$entry" -ef "$ACTIVE_DIR" ]] && continue
        built_here "${entry}/proj.db" && continue
        echo "${entry}/proj.db"
        return 0
    done
    if [[ -f "$INSTALLED_DB" ]] && ! built_here "$INSTALLED_DB"; then
        echo "$INSTALLED_DB"
    fi
}

# Point local/proj-data/proj.db at a database, replacing the link atomically.
activate() {
    local target="$1"
    if [[ -L "$ACTIVE_DIR" ]]; then
        echo "warn: local/proj-data is a symlink; not linking a database into it" >&2
        return 1
    fi
    mkdir -p "$ACTIVE_DIR"
    # A file written by link-local-grids.sh --patched-copy is a developer's
    # choice; report() says it is left in use.
    [[ -e "$ACTIVE_DB" && ! -L "$ACTIVE_DB" ]] && return 0
    local staged="${ACTIVE_DIR}/.proj.db.link.$$"
    ln -sfn -- "$target" "$staged"
    mv -fT -- "$staged" "$ACTIVE_DB"
}

# The PROJ release and database layout a database was made for, as PROJ
# records them in its metadata; a build keeps those of the base it started
# from. Empty when they cannot be read.
release_of() {
    query "$1" "SELECT 'PROJ ' || p.value || ', layout ' || major.value || '.' || minor.value FROM metadata p, metadata major, metadata minor WHERE p.key = 'PROJ.VERSION' AND major.key = 'DATABASE.LAYOUT.VERSION.MAJOR' AND minor.key = 'DATABASE.LAYOUT.VERSION.MINOR'"
}

# Whether build/proj.db exists and was built for the installed PROJ: from a
# base of the same release and layout as the stock database. With no stock
# database to compare with, an existing build is taken as it is.
build_usable() {
    [[ -f "$BUILT_DB" ]] || return 1
    local stock built
    stock="$(stock_db)"
    [[ -n "$stock" ]] || return 0
    built="$(release_of "$BUILT_DB")"
    [[ -n "$built" && "$built" == "$(release_of "$stock")" ]]
}

# Why build/proj.db cannot be used with the installed PROJ, as a clause.
mismatch() {
    local built
    built="$(release_of "$BUILT_DB")"
    echo "build/proj.db was built for ${built:-an unreadable PROJ release}," \
        "but the installed PROJ is $(release_of "$(stock_db)")"
}

# Move a build/proj.db made for another PROJ aside, so the official catalogue
# is built from scratch instead of being appended to it. Under the lock
# build-projdb.sh publishes under, so no build is reading it meanwhile.
retire_stale_build() {
    [[ -f "$BUILT_DB" ]] || return 0
    build_usable && return 0
    local reason lock
    reason="$(mismatch)"
    exec {lock}>"${BUILT_DB}.lock"
    flock "$lock"
    mv -f -- "$BUILT_DB" "$STALE_DB"
    exec {lock}>&-
    echo "note: ${reason}; moved it to build/proj.db.stale and building the" \
        "official OSDU catalogue from scratch. Anything another source" \
        "imported into it, the Georepository register for one, has to be" \
        "built again with scripts/build-projdb.sh." >&2
}

# Point local/proj-data/proj.db at build/proj.db when it was built for the
# installed PROJ, and at the stock database otherwise, so PROJ always reads a
# database it can open. Something must be there before any Python runs: pyproj
# reads the database from the first entry of PROJ_DATA, and warns when there is
# none there.
link_active() {
    if build_usable; then
        activate "$BUILT_DB_LINK"
        return
    fi
    local stock
    stock="$(stock_db)"
    if [[ -n "$stock" ]]; then
        activate "$stock"
    else
        echo "warn: no stock proj.db found on PROJ_DATA to link into local/proj-data" >&2
    fi
}

warn_box() {
    local headline="$1"
    shift
    {
        echo
        echo "=============================================================================="
        echo "WARNING: ${headline}"
        printf '  %s\n' "$@"
        echo "=============================================================================="
        echo
    } >&2
}

warn_no_osdu() {
    local reason="$1" reading="$2"
    warn_box "the OSDU catalogue is NOT installed in the proj.db PROJ reads." \
        "$reason" \
        "No OSDU CRSs, bound CRSs, transformations, record ids or persistable" \
        "references are available. PROJ reads ${reading}," \
        "so everything else works as usual." \
        "To retry:        scripts/install-osdu-projdb.sh" \
        "Newest from OSDU: scripts/install-osdu-projdb.sh --update"
}

first_on_proj_data() {
    local first="${PROJ_DATA:-}"
    first="${first%%:*}"
    [[ -n "$first" && -d "$ACTIVE_DIR" && "$first" -ef "$ACTIVE_DIR" ]]
}

# Report whether the database PROJ reads holds the OSDU catalogue.
report() {
    local failure="$1" reading ids references
    if ! first_on_proj_data; then
        echo "note: local/proj-data is not first on PROJ_DATA, so PROJ does not" \
            "read the database linked there. Use:" \
            "export PROJ_DATA=${ACTIVE_DIR}:\$PROJ_DATA" >&2
    fi
    if [[ -L "$ACTIVE_DB" ]]; then
        reading="$(readlink "$ACTIVE_DB")"
        [[ "$reading" == "$BUILT_DB_LINK" ]] && reading="build/proj.db"
    else
        reading="local/proj-data/proj.db"
        if [[ -f "$ACTIVE_DB" ]]; then
            echo "note: local/proj-data/proj.db is a copy written by hand" \
                "(link-local-grids.sh --patched-copy); leaving it in use." \
                "Remove it and rerun this script to read build/proj.db." >&2
        fi
    fi
    ids="$(osdu_ids "$ACTIVE_DB")"
    if [[ "$ids" -gt 0 ]]; then
        references="$(osdu_references "$ACTIVE_DB")"
        if [[ -n "$failure" ]]; then
            warn_box "the official OSDU catalogue could not be installed or updated." \
                "$failure" \
                "PROJ reads ${reading}, which holds the OSDU catalogue a build put" \
                "there. To retry: scripts/install-osdu-projdb.sh"
        fi
        echo "OSDU catalogue installed: ${reading} records ${ids} OSDU record id(s)" \
            "and ${references} persistable reference(s)"
    else
        warn_no_osdu "${failure:-${reading} records no OSDU catalogue.}" "$reading"
    fi
}

if $check_only; then
    link_active || true
    if [[ -f "$BUILT_DB" ]] && ! build_usable; then
        echo "note: $(mismatch), so PROJ does not read it; run" \
            "scripts/install-osdu-projdb.sh to build it again" >&2
    elif [[ -f "$DEFAULT_CATALOG" && -f "$BUILT_DB" ]]; then
        committed="$(version_of "$DEFAULT_CATALOG")"
        if ! holds_version "$BUILT_DB" "$committed"; then
            echo "note: build/proj.db does not hold the committed OSDU catalogue" \
                "(${committed}); run scripts/install-osdu-projdb.sh to import it" >&2
        fi
    fi
    report ""
    exit 0
fi

retire_stale_build
link_active || true

failure=""
if $update; then
    before=""
    [[ -f "$DEFAULT_CATALOG" ]] && before="$(version_of "$DEFAULT_CATALOG")"
    echo "==> updating ${DEFAULT_CATALOG#"${REPO_ROOT}/"} to the newest OSDU catalogue"
    if "${runner[@]}" geodetic-osdudb download --output "$DEFAULT_CATALOG" \
        --ref "${ref:-master}" >/dev/null; then
        after="$(version_of "$DEFAULT_CATALOG")"
        if [[ "$after" == "$before" ]]; then
            echo "the committed OSDU catalogue is already the newest (${after})"
        else
            echo "UPDATED the committed OSDU catalogue: ${before:-none} -> ${after}." \
                "Review and commit catalogs/osdu/."
        fi
    else
        failure="Could not update the OSDU catalogue; see the error above. The committed one was used."
    fi
fi

catalog="${catalog:-$DEFAULT_CATALOG}"
if [[ ! -f "$catalog" ]]; then
    failure="The official OSDU catalogue ${catalog#"${REPO_ROOT}/"} does not exist."
else
    version="$(version_of "$catalog")"
    if ! $force && holds_version "$BUILT_DB" "$version"; then
        echo "build/proj.db already holds the official OSDU catalogue (${version});" \
            "--force imports it again"
    else
        build=(--source osdu --catalog "$catalog")
        # Keep what other sources imported. A changed official catalogue
        # replaces what it restates, custom definitions included; those are
        # built on top again below.
        [[ -f "$BUILT_DB" ]] && build+=(--append --overwrite-rows)
        echo "==> building the official OSDU catalogue (${version}) into build/proj.db"
        if ! GEODETIC_ENGINE_OSDU_CATALOG_VERSION="$version" \
            bash "${REPO_ROOT}/scripts/build-projdb.sh" "${build[@]}"; then
            failure="Building the OSDU catalogue into build/proj.db failed; see the log above and build/proj.db.log."
        fi
    fi
fi

# Custom catalogues go on top every time, so they stay above the official one
# whenever it is imported again.
unapplied=()
for custom in ${customs[@]+"${customs[@]}"}; do
    shown="${custom#"${REPO_ROOT}/"}"
    authorities=()
    spaces=""
    if [[ -f "$custom" ]]; then
        while IFS= read -r space; do
            [[ -n "$space" ]] || continue
            authorities+=(--authority "$space")
            spaces+="${spaces:+, }${space}"
        done < <(code_spaces "$custom" 2>/dev/null || true)
    fi
    echo "==> building the custom catalogue ${shown} (code spaces:" \
        "${spaces:-none but EPSG}) on top: a record it states differently" \
        "replaces the earlier definition, and is listed as REPLACED in the" \
        "build summary"
    if [[ ! -f "$custom" ]] || ! bash "${REPO_ROOT}/scripts/build-projdb.sh" \
        --source osdu --catalog "$custom" --append --overwrite-rows \
        ${authorities[@]+"${authorities[@]}"}; then
        unapplied+=("$shown")
    fi
done

link_active || true
report "$failure"
for shown in ${unapplied[@]+"${unapplied[@]}"}; do
    warn_box "the custom catalogue ${shown} was NOT applied." \
        "It does not exist, or its build failed; see the log above." \
        "build/proj.db is unchanged by it: its own CRSs and transformations are" \
        "not available, and the records it restates keep their earlier definition."
done
if [[ ${#customs[@]} -gt ${#unapplied[@]} ]]; then
    echo "Custom catalogue(s) on top of the official one:" \
        "$(( ${#customs[@]} - ${#unapplied[@]} )) applied; see each build's" \
        "summary for what they replaced"
fi

if [[ -n "$failure" || ${#unapplied[@]} -gt 0 ]] && $strict; then
    exit 1
fi
exit 0
