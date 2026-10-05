"""Build-time helpers for the geodetic-engine documentation.

* Writes one API reference page per public subpackage from its ``__all__``, so
  the reference cannot drift from what the package actually exports.
* Copies ``examples/*.ipynb`` into the source tree so myst-nb can execute them.
* Keeps dataclass fields that the class docstring already documents under
  ``Attributes:`` from being documented a second time by autodoc.
"""

from __future__ import annotations

import importlib
import inspect
import re
import shutil
from pathlib import Path
from typing import Any

from sphinx.application import Sphinx
from sphinx.config import Config

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

# (module, one-line summary shown on the API index)
SUBPACKAGES: tuple[tuple[str, str], ...] = (
    (
        "geodetic_engine.geodesy",
        "Transform coordinates, choose operations, read provenance, and "
        "evaluate projection factors.",
    ),
    (
        "geodetic_engine.geodesy.utils",
        "Helmert algebra and abridged-Molodensky helpers.",
    ),
    (
        "geodetic_engine.welltrajectory",
        "Well trajectories from directional surveys: minimum curvature, "
        "georeferencing in a CRS, 3D plots.",
    ),
    (
        "geodetic_engine.persistablereference",
        "Read and write OSDU persistableReference definitions.",
    ),
    (
        "geodetic_engine.georepository",
        "OAuth2 client for the Georepository geodetic registry API.",
    ),
    (
        "geodetic_engine.projdb",
        "Build an enriched proj.db from a Georepository instance.",
    ),
    (
        "geodetic_engine.osdudb",
        "Build an enriched proj.db from an OSDU catalogue.",
    ),
    ("geodetic_engine.errors", "The root of every exception the package raises."),
)

# Not exported, but public signatures return or accept them.
SUPPORTING: dict[str, tuple[str, ...]] = {
    "geodetic_engine.persistablereference": (
        "geodetic_engine.persistablereference.esriwkt.Node",
        "geodetic_engine.persistablereference.esriwkt.write",
    ),
    "geodetic_engine.osdudb": ("geodetic_engine.osdudb.catalog.Record",),
    "geodetic_engine.projdb": ("geodetic_engine.projdb.errors.OutputWouldBeDiscarded",),
}

# pyproj documents these under their defining module only.
ALIASES = {
    "pyproj.CRS": "pyproj.crs.CRS",
    "pyproj.Transformer": "pyproj.transformer.Transformer",
    "pyproj.Transformer.transform": "pyproj.transformer.Transformer.transform",
    "pyproj.Transformer.from_pipeline": "pyproj.transformer.Transformer.from_pipeline",
}

_GROUPS = (
    ("function", "Functions"),
    ("class", "Classes"),
    ("data", "Constants and type aliases"),
    ("exception", "Exceptions"),
)

_HEADER = "% Generated from __all__ by docs/_ext/geodetic_docs.py. Do not edit.\n\n"


def _kind(obj: Any) -> str:
    if inspect.isclass(obj):
        return "exception" if issubclass(obj, BaseException) else "class"
    if inspect.isroutine(obj):
        return "function"
    return "data"


def _owned(package: str, obj: Any) -> bool:
    """Whether ``obj`` is defined in ``package`` rather than re-exported."""
    named = (
        inspect.isclass(obj)
        or inspect.isroutine(obj)
        or type(obj).__name__ == "TypeAliasType"
    )
    if not named:
        return True
    module: str = obj.__module__
    return module == package or module.startswith(package + ".")


def _page(package: str) -> str:
    module = importlib.import_module(package)
    names: list[str] = list(getattr(module, "__all__", ())) or [
        name
        for name, obj in vars(module).items()
        if not name.startswith("_") and getattr(obj, "__module__", None) == package
    ]
    grouped: dict[str, list[str]] = {kind: [] for kind, _ in _GROUPS}
    reexported: list[tuple[str, str]] = []
    for name in sorted(names, key=str.lower):
        obj = getattr(module, name)
        if _owned(package, obj):
            grouped[_kind(obj)].append(name)
        else:
            reexported.append((name, _public_home(obj)))

    lines = [
        _HEADER,
        f"# `{package}`\n\n",
        "```{eval-rst}\n",
        f".. automodule:: {package}\n   :no-members:\n\n",
        f".. currentmodule:: {package}\n",
        "```\n\n",
    ]
    for kind, title in _GROUPS:
        if not grouped[kind]:
            continue
        lines += [
            f"## {title}\n\n",
            "```{eval-rst}\n",
            ".. autosummary::\n   :toctree: generated\n   :nosignatures:\n\n",
        ]
        lines += [f"   {name}\n" for name in grouped[kind]]
        lines += ["```\n\n"]
    if package in SUPPORTING:
        lines += [
            "## Supporting types\n\n",
            "Not exported from the subpackage, but returned or accepted by "
            "something that is.\n\n",
            "```{eval-rst}\n",
            ".. autosummary::\n   :toctree: generated\n   :nosignatures:\n\n",
        ]
        lines += [
            f"   {name.removeprefix(package + '.')}\n" for name in SUPPORTING[package]
        ]
        lines += ["```\n\n"]
    if reexported:
        lines += [
            "## Re-exported\n\n",
            "Importable from here for convenience; documented where they are "
            "defined.\n\n",
        ]
        lines += [f"- {{py:obj}}`{name} <{home}>`\n" for name, home in reexported]
        lines += ["\n"]
    return "".join(lines)


def _public_home(obj: Any) -> str:
    """Public dotted path of a re-exported object, via the owning subpackage."""
    module: str = obj.__module__
    owners = [p for p, _ in SUBPACKAGES if module.startswith(p + ".")]
    home = max(owners, key=len) if owners else module
    return f"{home}.{obj.__name__}"


def _index() -> str:
    lines = [
        _HEADER,
        "# API reference\n\n",
        "Every name listed here is part of the public API: it is in its "
        "subpackage's `__all__` and can be imported from the subpackage "
        "directly, for example `from geodetic_engine.geodesy import transform`. "
        "The top-level `geodetic_engine` package exports nothing but "
        "`__version__`.\n\n",
        "| Subpackage | Purpose |\n|---|---|\n",
    ]
    for package, summary in SUBPACKAGES:
        slug = package.removeprefix("geodetic_engine.")
        lines.append(f"| [`{package}`]({slug}.md) | {summary} |\n")
    lines += ["\n```{toctree}\n:hidden:\n\n"]
    lines += [
        f"{package.removeprefix('geodetic_engine.')}\n" for package, _ in SUBPACKAGES
    ]
    lines += ["```\n"]
    return "".join(lines)


def _write_if_changed(path: Path, text: str) -> None:
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def generate_api_pages(app: Sphinx, config: Config) -> None:
    target = DOCS / "api"
    target.mkdir(exist_ok=True)
    _write_if_changed(target / "index.md", _index())
    for package, _ in SUBPACKAGES:
        slug = package.removeprefix("geodetic_engine.")
        _write_if_changed(target / f"{slug}.md", _page(package))


def stage_notebooks(app: Sphinx, config: Config) -> None:
    target = DOCS / "examples" / "notebooks"
    target.mkdir(parents=True, exist_ok=True)
    for notebook in sorted((REPO / "examples").glob("*.ipynb")):
        destination = target / notebook.name
        if (
            not destination.exists()
            or destination.read_bytes() != notebook.read_bytes()
        ):
            shutil.copyfile(notebook, destination)


_ATTRIBUTES = re.compile(
    r"^\s*Attributes:\s*$(.*?)(?:^\s*\w[\w ]*:\s*$|\Z)", re.M | re.S
)
_ATTRIBUTE_NAME = re.compile(r"^\s{4,}(\w+)(?:\s*\(.*?\))?:", re.M)


def _documented_attributes(cls: type) -> frozenset[str]:
    match = _ATTRIBUTES.search(inspect.getdoc(cls) or "")
    if not match:
        return frozenset()
    return frozenset(_ATTRIBUTE_NAME.findall(match.group(1)))


def skip_attributes_section_duplicates(
    app: Sphinx, what: str, name: str, obj: Any, skip: bool, options: Any
) -> bool | None:
    if what != "class":
        return None
    env = app.env
    class_name = env.temp_data.get("autodoc:class")
    module_name = env.temp_data.get("autodoc:module") or env.ref_context.get(
        "py:module"
    )
    if not class_name or not module_name:
        return None
    owner = getattr(importlib.import_module(module_name), class_name, None)
    if inspect.isclass(owner) and name in _documented_attributes(owner):
        return True
    return None


def _public_candidates(target: str) -> list[str]:
    """Public spellings of a target named by its defining module's path.

    ``geodetic_engine.geodesy.result.TransformationResult.pipeline`` is
    documented as ``geodetic_engine.geodesy.TransformationResult.pipeline``.
    """
    owners = [p for p, _ in SUBPACKAGES if target.startswith(p + ".")]
    if not owners:
        return []
    package = max(owners, key=len)
    parts = target[len(package) + 1 :].split(".")
    return [".".join((package, *parts[i:])) for i in range(1, len(parts))]


def _is_float_array(target: str) -> bool:
    """A module's own ``type FloatArray = NDArray[np.float64]``, named by autodoc."""
    return target.startswith("geodetic_engine.") and target.endswith(".FloatArray")


def resolve_private_paths(
    app: Sphinx, env: Any, node: Any, contnode: Any
) -> Any | None:
    if node.get("refdomain") != "py":
        return None
    target: str = node["reftarget"]
    domain = env.get_domain("py")
    builder = app.builder
    reftype = node["reftype"]

    if target in ALIASES or _is_float_array(target):
        from sphinx.ext.intersphinx import missing_reference

        node["reftarget"] = ALIASES.get(target, "numpy.typing.NDArray")
        node["reftype"] = "obj"
        resolved = missing_reference(app, env, node, contnode)
        node["reftarget"], node["reftype"] = target, reftype
        return resolved

    candidates = _public_candidates(target)
    if reftype == "mod":
        parts = target.split(".")
        candidates = [".".join(parts[:i]) for i in range(len(parts) - 1, 1, -1)]
    for candidate in candidates:
        if candidate in domain.objects or (
            reftype == "mod" and candidate in domain.modules
        ):
            return domain.resolve_xref(
                env, node["refdoc"], builder, reftype, candidate, node, contnode
            )
    return None


def setup(app: Sphinx) -> dict[str, Any]:
    app.connect("config-inited", generate_api_pages)
    app.connect("config-inited", stage_notebooks)
    app.connect("autodoc-skip-member", skip_attributes_section_duplicates)
    app.connect("missing-reference", resolve_private_paths)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
