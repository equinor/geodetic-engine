"""Which subpackages a subpackage loads, in a fresh interpreter."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# Imports argv[1] and all its modules, then prints those loaded under argv[2:].
_LOADED = """
import importlib, pkgutil, sys
package = importlib.import_module(sys.argv[1])
for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
    importlib.import_module(module.name)
forbidden = sys.argv[2:]
for name in sorted(sys.modules):
    if any(name == other or name.startswith(other + ".") for other in forbidden):
        print(name)
"""


@pytest.mark.parametrize(
    ("package", "forbidden"),
    [
        ("geodetic_engine.geojson", ["geodetic_engine.welltrajectory"]),
        (
            "geodetic_engine.units",
            [
                "geodetic_engine.welltrajectory",
                "geodetic_engine.geojson",
                "geodetic_engine.bingrid",
            ],
        ),
    ],
)
def test_a_package_does_not_load_the_domains_it_is_independent_of(
    package: str, forbidden: list[str]
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _LOADED, package, *forbidden],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == []
