ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG PROJ_VERSION
ARG PROJ_SHA256
ARG PYPROJ_REVISION

WORKDIR /opt/pyproj-native-build
COPY proj-compile-wheels.sh ./
COPY proj.tar.gz archives/proj.tar.gz

RUN set -eux; \
    test -n "$PROJ_VERSION"; test -n "$PROJ_SHA256"; test -n "$PYPROJ_REVISION"; \
    mv archives/proj.tar.gz "archives/proj-${PROJ_VERSION}.tar.gz"; \
    printf '%s  %s\n' "$PROJ_SHA256" "archives/proj-${PROJ_VERSION}.tar.gz" | sha256sum --check --strict -; \
    PROJ_WHEEL=true PROJ_VERSION="$PROJ_VERSION" PROJ_DIR=/project/pyproj-src/pyproj/proj_dir \
      bash -e proj-compile-wheels.sh; \
    mkdir -p /opt/pyproj-native; \
    mv /project/pyproj-src/pyproj/proj_dir /opt/pyproj-native/proj; \
    printf '%s\n%s\n%s\n' "$PROJ_VERSION" "$PROJ_SHA256" "$PYPROJ_REVISION" > /opt/pyproj-native/build-inputs; \
    cd /; \
    rm -rf /opt/pyproj-native-build

WORKDIR /
COPY pyproj_native_cache.py /opt/pyproj-native-check.py
RUN python3 /opt/pyproj-native-check.py verify linux /opt/pyproj-native/proj "$PROJ_VERSION" \
    && rm /opt/pyproj-native-check.py
