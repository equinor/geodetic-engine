FROM python:3.13-slim-trixie

ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        git-lfs \
        less \
        sudo \
        build-essential \
        cmake \
        ninja-build \
        pkg-config \
        libsqlite3-dev \
        sqlite3 \
        libtiff-dev \
        libcurl4-openssl-dev \
        zlib1g-dev \
    && groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID --create-home --shell /bin/bash $USERNAME \
    && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME \
    && git lfs install --system --skip-repo \
    && rm -rf /var/lib/apt/lists/*

# PROJ is built from source so the version is pinned and reproducible. See the
# script for why the resource files must not be embedded into libproj.
COPY install-proj.sh /tmp/install-proj.sh
RUN chmod +x /tmp/install-proj.sh \
    && /tmp/install-proj.sh \
    && rm /tmp/install-proj.sh

# PROJ_DIR makes pyproj build against this PROJ instead of vendoring its own.
ENV PROJ_DIR=/usr/local \
    PROJ_DATA=/usr/local/share/proj \
    PROJ_WHEEL=false

# uv/uvx for packaging, locking and publishing
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

USER $USERNAME
