#!/usr/bin/env bash
#
# Build one PROJ database from the Georepository register, the OSDU catalogue,
# or both.
#
# Both sources write into the same file. The first build selected starts from
# the official proj.db; every build after it is passed --append, so it adds its
# authority to what the previous one wrote instead of starting over. That is
# why the output is removed first unless --extend is given: appending to a
# database left over from an earlier run would silently mix two generations of
# definitions in one file.
#
# Each build keeps its own report and log beside the database, named after the
# source that wrote it, so the provenance of a combined build is not lost to
# whichever source happened to run last.
#
# Usage:
#   scripts/build-projdb.sh [options]
#
# Run with --help for the options.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly INVOCATION_DIR="$PWD"

source_kind="both"
output="build/proj.db"
output_given=false
catalog=""
georepository_config=""
osdu_config=""
osdu_authorities=()
overwrite_existing=false
skip_validation=false
dry_run=false
extend=false
verbose=false

usage() {
    cat <<'EOF'
Build a PROJ database enriched from the Georepository register, an OSDU
catalogue, or both, into a single file.

Options:
  -s, --source KIND       Which source(s) to import: georepository, osdu or
                          both. Default: both.
  -o, --output PATH       Database to write. Default: build/proj.db.
  -c, --catalog PATH      OSDU manifest to read, for example CRS_CT.json.
                          Falls back to the catalog set in the config file.
      --georepository-config PATH
                          TOML file with a [projdb] table. Default: discovered
                          as geodetic-projdb.toml in the working directory.
      --osdu-config PATH  TOML file with an [osdudb] table. Default: discovered
                          as geodetic-osdudb.toml in the working directory.
  -a, --authority NAME    Code space to import from the OSDU catalogue;
                          repeatable. Defaults to OSDU. Add EPSG to also import
                          the catalogue's EPSG objects that this proj.db does
                          not already define.
      --overwrite-existing
                          Replace a colliding row of a build's own authorities
                          instead of aborting. Another authority's rows are
                          never touched either way. Off by default.
      --extend            Add to the database already at --output instead of
                          removing it first. Use to add a source to a database
                          built by an earlier run.
      --skip-validation   Write without checking that PROJ can read the result
                          back. Not recommended.
      --dry-run           Run every build and report what each would write,
                          then discard it. Nothing is left on disk.
  -v, --verbose           Log every imported object.
  -h, --help              Show this message.

Examples:
  # Fresh database from both sources
  scripts/build-projdb.sh --catalog CRS_CT.json

  # Georepository only, somewhere else
  scripts/build-projdb.sh --source georepository --output /tmp/proj.db

  # Add OSDU to a database an earlier run already built
  scripts/build-projdb.sh --source osdu --catalog CRS_CT.json --extend

Georepository credentials are never passed as arguments. Set them in a
gitignored .env file or in the environment as GEODETIC_ENGINE_GEOREP_CLIENT_ID
and GEODETIC_ENGINE_GEOREP_CLIENT_SECRET.
EOF
}

die() {
    echo "error: $*" >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s | --source)
            source_kind="${2:-}"
            shift 2
            ;;
        -o | --output)
            output="${2:-}"
            output_given=true
            shift 2
            ;;
        -c | --catalog)
            catalog="${2:-}"
            shift 2
            ;;
        --georepository-config)
            georepository_config="${2:-}"
            shift 2
            ;;
        --osdu-config)
            osdu_config="${2:-}"
            shift 2
            ;;
        -a | --authority)
            osdu_authorities+=("${2:-}")
            shift 2
            ;;
        --overwrite-existing)
            overwrite_existing=true
            shift
            ;;
        --extend)
            extend=true
            shift
            ;;
        --skip-validation)
            skip_validation=true
            shift
            ;;
        --dry-run)
            dry_run=true
            shift
            ;;
        -v | --verbose)
            verbose=true
            shift
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

case "$source_kind" in
    georepository) sources=(georepository) ;;
    osdu) sources=(osdu) ;;
    both) sources=(georepository osdu) ;;
    *) die "--source must be georepository, osdu or both, not '${source_kind}'" ;;
esac

[[ -n "$output" ]] || die "--output needs a path"

# Both builders look for their TOML config in the working directory, so the
# builds have to run from the repository root to find the ones kept there.
# Paths the caller gave are resolved against the directory they were typed in
# first, since that is where they mean; the default output is relative to the
# repository, which is where build/ is.
absolute() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s\n' "${INVOCATION_DIR}/$1" ;;
    esac
}

if $output_given; then
    output="$(absolute "$output")"
else
    output="${REPO_ROOT}/${output}"
fi
if [[ -n "$catalog" ]]; then
    catalog="$(absolute "$catalog")"
fi
if [[ -n "$georepository_config" ]]; then
    georepository_config="$(absolute "$georepository_config")"
fi
if [[ -n "$osdu_config" ]]; then
    osdu_config="$(absolute "$osdu_config")"
fi

cd "$REPO_ROOT"

# uv keeps the project's own environment, which is where the entry points live.
# Without it they have to already be on PATH, which is the case in an activated
# virtualenv.
if [[ -n "${GEODETIC_RUNNER:-}" ]]; then
    read -r -a runner <<<"$GEODETIC_RUNNER"
elif command -v uv >/dev/null 2>&1 && [[ -f "${REPO_ROOT}/pyproject.toml" ]]; then
    runner=(uv run --project "$REPO_ROOT")
else
    runner=()
fi

for entry_point in geodetic-projdb geodetic-osdudb; do
    if [[ ${#runner[@]} -eq 0 ]] && ! command -v "$entry_point" >/dev/null 2>&1; then
        die "$entry_point is not on PATH; activate the project's virtualenv, or install uv"
    fi
done

common_args=()
$skip_validation && common_args+=(--skip-validation)
$dry_run && common_args+=(--dry-run)
$overwrite_existing && common_args+=(--overwrite-existing)
verbose_args=()
$verbose && verbose_args+=(--verbose)

if ! $extend && ! $dry_run; then
    # Sidecars too: a report describing a database that no longer exists is
    # worse than no report, because it still reads as a description of this one.
    for stale in "$output" "$output".*.report.json "$output".*.log \
        "$output".report.json "$output".log; do
        if [[ -e "$stale" ]]; then
            echo "removing $stale"
            rm -f -- "$stale"
        fi
    done
fi

# The first build to run creates the database; every one after it adds to what
# is already there. With --extend the very first one adds too.
append=$extend

for source in "${sources[@]}"; do
    args=("${common_args[@]}" --output "$output")
    $append && args+=(--append)

    case "$source" in
        georepository)
            [[ -n "$georepository_config" ]] && args+=(--config "$georepository_config")
            echo "==> building from the Georepository register into $output"
            "${runner[@]}" geodetic-projdb "${verbose_args[@]}" build "${args[@]}"
            ;;
        osdu)
            [[ -n "$osdu_config" ]] && args+=(--config "$osdu_config")
            for authority in ${osdu_authorities[@]+"${osdu_authorities[@]}"}; do
                args+=(--authority "$authority")
            done
            [[ -n "$catalog" ]] && args+=("$catalog")
            echo "==> building from the OSDU catalogue into $output"
            "${runner[@]}" geodetic-osdudb "${verbose_args[@]}" build "${args[@]}"
            ;;
    esac

    append=true
done

if $dry_run; then
    echo "dry run: nothing was written to $output"
    exit 0
fi

echo
echo "==> $output now holds:"
"${runner[@]}" geodetic-projdb inspect "$output"
