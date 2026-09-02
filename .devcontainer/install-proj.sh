#!/usr/bin/env bash
#
# Build and install PROJ from source at a pinned version.
#
# PROJ is built from source rather than installed from apt so that the version
# is reproducible across the devcontainer, CI and contributor machines. Debian
# trixie ships an older PROJ, and pyproj's PyPI wheels bundle their own copy;
# neither is acceptable when the exact EPSG dataset version baked into proj.db
# is part of the answer we give callers.
set -euo pipefail

PROJ_VERSION="${PROJ_VERSION:-9.8.1}"
PROJ_SHA256="${PROJ_SHA256:-af5b731c145c1d13c4e3b4eeb7d167e94e845e440f71e3496b4ed8dae0291960}"
PROJ_PREFIX="${PROJ_PREFIX:-/usr/local}"

# The transformation grids, which a PROJ source build does not include. The
# version matches the PROJ_DATA.VERSION recorded in this PROJ's proj.db.
# Set SKIP_PROJ_DATA=1 to omit them; the download is roughly 750 MB.
PROJ_DATA_VERSION="${PROJ_DATA_VERSION:-1.24}"
PROJ_DATA_SHA256="${PROJ_DATA_SHA256:-eadf412754a2a9a727d79579873fbe7dae802038d4c2a19e452a886d4eddd111}"

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT
cd "${workdir}"

tarball="proj-${PROJ_VERSION}.tar.gz"
curl -fsSL --retry 3 -o "${tarball}" \
    "https://download.osgeo.org/proj/${tarball}"

echo "${PROJ_SHA256}  ${tarball}" | sha256sum --check --strict -

tar xzf "${tarball}"

# EMBED_RESOURCE_FILES=OFF is required: when proj.db is embedded into libproj,
# a custom database on disk pointed to by PROJ_DATA can be silently ignored.
cmake -G Ninja -S "proj-${PROJ_VERSION}" -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${PROJ_PREFIX}" \
    -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTING=OFF \
    -DBUILD_APPS=ON \
    -DENABLE_TIFF=ON \
    -DENABLE_CURL=ON \
    -DEMBED_RESOURCE_FILES=OFF \
    -DUSE_ONLY_EMBEDDED_RESOURCE_FILES=OFF

cmake --build build -j "$(nproc)"
cmake --install build
ldconfig

installed="$(pkg-config --modversion proj)"
if [[ "${installed}" != "${PROJ_VERSION}" ]]; then
    echo "PROJ version mismatch: built ${PROJ_VERSION}, found ${installed}" >&2
    exit 1
fi
echo "PROJ ${installed} installed to ${PROJ_PREFIX}"

# Grids are not optional: without them a transformation that needs one either
# fails or silently degrades, so they are installed with PROJ rather than left
# for the user to discover they are missing.
if [[ "${SKIP_PROJ_DATA:-0}" == "1" ]]; then
    echo "skipping proj-data at the caller's request; grid-based transformations" \
         "will report their grids as unavailable" >&2
    exit 0
fi

data_tarball="proj-data-${PROJ_DATA_VERSION}.tar.gz"
curl -fsSL --retry 3 -o "${data_tarball}" \
    "https://download.osgeo.org/proj/${data_tarball}"
echo "${PROJ_DATA_SHA256}  ${data_tarball}" | sha256sum --check --strict -

# The archive is flat and extracts directly into the PROJ data directory.
tar xzf "${data_tarball}" -C "${PROJ_PREFIX}/share/proj"
echo "proj-data ${PROJ_DATA_VERSION} installed to ${PROJ_PREFIX}/share/proj"
