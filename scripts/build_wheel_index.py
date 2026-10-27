"""Write the package index that serves this repository's pyproj wheels.

The wheels are assets of the GitHub releases tagged pyproj-*, published by
.github/workflows/pyproj-wheels.yml with a SHA256SUMS file beside them. This
writes a PEP 503 simple index of them, which the documentation workflow puts
on GitHub Pages under simple/:

    python scripts/build_wheel_index.py equinor/geodetic-engine site/simple

GH_TOKEN, when set, authenticates the GitHub API requests.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

RELEASE_PREFIX = "pyproj-"
CHECKSUMS = "SHA256SUMS"


def _fetch(url: str, *, api: bool) -> bytes:
    headers = {"Accept": "application/vnd.github+json"} if api else {}
    token = os.environ.get("GH_TOKEN")
    # Asset downloads redirect to storage that rejects a GitHub token.
    if api and token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        body: bytes = response.read()
    return body


def _releases(repository: str) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}"
        batch = json.loads(_fetch(url, api=True))
        if not batch:
            return releases
        releases.extend(batch)
        page += 1


def _checksums(text: str) -> dict[str, str]:
    """Parse sha256sum output into {file name: hex digest}."""
    digests = {}
    for line in text.splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            digests[name.lstrip("*")] = digest
    return digests


def wheel_links(repository: str) -> list[tuple[str, str]]:
    """Return (file name, download URL with #sha256=) for every published wheel."""
    links = []
    for release in _releases(repository):
        tag = release["tag_name"]
        if release["draft"] or not tag.startswith(RELEASE_PREFIX):
            continue
        assets = {a["name"]: a["browser_download_url"] for a in release["assets"]}
        if CHECKSUMS not in assets:
            raise SystemExit(f"release {tag} has no {CHECKSUMS}")
        digests = _checksums(_fetch(assets[CHECKSUMS], api=False).decode())
        for name, url in assets.items():
            if not name.endswith(".whl"):
                continue
            if name not in digests:
                raise SystemExit(f"{name} in release {tag} is not in its {CHECKSUMS}")
            links.append((name, f"{url}#sha256={digests[name]}"))
    return sorted(links)


def _page(title: str, links: list[tuple[str, str]]) -> str:
    anchors = "".join(
        f'<a href="{html.escape(url)}">{html.escape(name)}</a><br>\n'
        for name, url in links
    )
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="pypi:repository-version" content="1.0">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"</head>\n<body>\n{anchors}</body>\n</html>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repository", help="owner/name of the repository")
    parser.add_argument("output", type=Path, help="directory to write the index to")
    args = parser.parse_args()

    links = wheel_links(args.repository)
    project = args.output / "pyproj"
    project.mkdir(parents=True, exist_ok=True)
    (args.output / "index.html").write_text(
        _page("Simple index", [("pyproj", "pyproj/")]), encoding="utf-8"
    )
    (project / "index.html").write_text(
        _page("Links for pyproj", links), encoding="utf-8"
    )
    print(f"{len(links)} pyproj wheel(s) indexed in {args.output}")


if __name__ == "__main__":
    main()
