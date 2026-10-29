"""Keep every copy of the pinned versions in line with .devcontainer/pins.env.

    python scripts/sync_pins.py            # rewrite the copies, then uv lock
    python scripts/sync_pins.py --check    # change nothing; fail on a stale copy
    python scripts/sync_pins.py --get KEY  # print one pin, for workflows

.devcontainer/pins.env is the one place the PROJ, proj-data and pyproj releases
are stated. Whatever can read it does: install-proj.sh, the workflows, the
release scripts, and geodetic_engine through the copy the wheel ships. COPIES
below lists the rest: text that GitHub, PyPI, pip and uv read as written. Only
those spots are rewritten, so history such as CHANGELOG.md is never touched.
--check fails, naming the file, when a copy is stale or a spot can no longer be
found; the test suite runs it. See runbooks/upgrading-proj-and-pyproj.md.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PINS_FILE = Path(".devcontainer/pins.env")

_REQUIREMENT = re.compile(
    r"pyproj==\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?\+proj\d+(?:\.\d+)*"
)
_INDEX = re.compile(r"(?<=--extra-index-url )https://[^\s\"'`\\]+")
# The documents whose install command names the pyproj wheel and its index.
_INSTALL_COMMANDS = (
    "README.md",
    "docs/getting-started/installation.md",
    "docs/user-guide/welltrajectory/plotting.md",
    "geodetic_engine/welltrajectory/README.md",
    "examples/welltrajectory_examples.ipynb",
)


def pins_module() -> ModuleType:
    """geodetic_engine/_pins.py, loaded without importing the package."""
    path = REPO_ROOT / "geodetic_engine" / "_pins.py"
    spec = importlib.util.spec_from_file_location("geodetic_engine_pins", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_pins(root: Path = REPO_ROOT) -> Any:
    """The pins a checkout states, refusing a malformed file."""
    path = root / PINS_FILE
    return pins_module().parse(path.read_text(encoding="utf-8"), str(path))


@dataclass(frozen=True)
class Copy:
    """One spot in one file that states a pinned value."""

    path: str
    what: str
    pattern: re.Pattern[str]
    value: Callable[[Any, re.Match[str]], str]


def _copies() -> tuple[Copy, ...]:
    copies = [
        Copy(
            "pyproject.toml",
            "the pyproj dependency",
            re.compile(r'^(?P<indent>[ \t]*)"pyproj==[^"]+",$', re.MULTILINE),
            lambda pins, match: f'{match["indent"]}"pyproj=={pins.pyproj_version}",',
        ),
        Copy(
            "README.md",
            "the PROJ release",
            re.compile(r"\*\*PROJ [^*\s]+\*\*"),
            lambda pins, _: f"**PROJ {pins.proj_version}**",
        ),
        Copy(
            "README.md",
            "the pyproj release",
            re.compile(r"\*\*pyproj [^*\s]+ built against it\*\*"),
            lambda pins, _: f"**pyproj {pins.pyproj_version} built against it**",
        ),
        Copy(
            "docs/getting-started/installation.md",
            "the pyproj source build",
            re.compile(r'(?<=--no-binary=pyproj )"pyproj==[^"]+"'),
            lambda pins, _: f'"pyproj=={pins.pyproj_version}"',
        ),
    ]
    for path in _INSTALL_COMMANDS:
        copies.append(
            Copy(
                path,
                "the pyproj wheel",
                _REQUIREMENT,
                lambda pins, _: pins.pyproj_requirement,
            )
        )
        copies.append(
            Copy(
                path,
                "the pyproj wheel index",
                _INDEX,
                lambda pins, _: pins.pyproj_wheel_index,
            )
        )
    return tuple(copies)


COPIES = _copies()


def sync(*, write: bool, root: Path = REPO_ROOT) -> list[str]:
    """Bring every copy in line with the pins.

    Args:
        write: Rewrite stale files; otherwise only report them.
        root: The checkout.

    Returns:
        The files that were, or would be, rewritten.

    Raises:
        ValueError: If the pins file is malformed, or a spot is not found
            exactly once, which means COPIES no longer matches the file.
    """
    pins = read_pins(root)
    texts: dict[str, str] = {}
    for copy in COPIES:
        text = texts.setdefault(
            copy.path, (root / copy.path).read_text(encoding="utf-8")
        )
        text, count = copy.pattern.subn(partial(copy.value, pins), text)
        if count != 1:
            raise ValueError(
                f"{copy.path}: found {copy.what} {count} times, not once; update "
                "COPIES in scripts/sync_pins.py to where it is stated now"
            )
        texts[copy.path] = text
    stale = []
    for path, text in texts.items():
        target = root / path
        if target.read_text(encoding="utf-8") != text:
            stale.append(path)
            if write:
                with target.open("w", encoding="utf-8", newline="") as stream:
                    stream.write(text)
    return stale


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check", action="store_true", help="change nothing; fail if stale"
    )
    action.add_argument(
        "--get",
        metavar="KEY",
        help="print one pin, or PYPROJ_REQUIREMENT, and exit",
    )
    args = parser.parse_args()
    try:
        if args.get:
            print(read_pins().get(args.get))
            return
        stale = sync(write=not args.check)
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error

    if args.check:
        if stale:
            raise SystemExit(
                "stale copies of .devcontainer/pins.env: "
                f"{', '.join(stale)}; run python scripts/sync_pins.py"
            )
        print("every copy matches .devcontainer/pins.env")
        return
    for path in stale:
        print(f"rewrote {path}")
    if "pyproject.toml" in stale:
        if shutil.which("uv") is None:
            print("pyproject.toml changed: run uv lock before committing")
        else:
            subprocess.run(["uv", "lock"], cwd=REPO_ROOT, check=True)
    if not stale:
        print("every copy already matches .devcontainer/pins.env")


if __name__ == "__main__":
    main()
