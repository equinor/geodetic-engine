"""Sphinx configuration for the geodetic-engine documentation."""

from __future__ import annotations

import sys
from pathlib import Path

import pyproj

DOCS = Path(__file__).resolve().parent
sys.path.insert(0, str(DOCS / "_ext"))

import geodetic_engine  # noqa: E402

project = "geodetic-engine"
author = "Equinor"
copyright = "Equinor ASA"
version = geodetic_engine.__version__
release = version

REPOSITORY = "https://github.com/equinor/geodetic-engine"
PAGES = "https://equinor.github.io/geodetic-engine"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.doctest",
    "myst_nb",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinxcontrib.mermaid",
    "sphinxarg.ext",
    "geodetic_docs",
]

exclude_patterns = ["_build", "jupyter_execute", "**.ipynb_checkpoints"]
templates_path = ["_templates"]
html_static_path = ["_static"]
html_css_files = ["custom.css"]

# -- Cross-references ---------------------------------------------------------

nitpicky = True
nitpick_ignore = [
    # httpx publishes its documentation with MkDocs, so has no Sphinx inventory.
    ("py:class", "httpx.Client"),
    ("py:class", "httpx.BaseTransport"),
    ("py:class", "httpx.Response"),
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pyproj": ("https://pyproj4.github.io/pyproj/stable", None),
    "numpy": ("https://numpy.org/doc/stable", None),
    "pandas": ("https://pandas.pydata.org/docs", None),
}

# -- API reference ------------------------------------------------------------

autosummary_generate = True
autosummary_ignore_module_all = False
autosummary_imported_members = True
autodoc_member_order = "bysource"
autoclass_content = "both"
autodoc_class_signature = "separated"
autodoc_preserve_defaults = True

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_rtype = False
napoleon_attr_annotations = True

typehints_use_signature = False
typehints_use_signature_return = False
always_use_bars_union = True
typehints_defaults = "comma"

# -- Executed examples --------------------------------------------------------

myst_enable_extensions = ["colon_fence", "deflist", "fieldlist", "substitution"]
myst_heading_anchors = 3
myst_fence_as_directive = ["mermaid"]
# The extension otherwise forces every diagram to 500px tall.
mermaid_height = "auto"
mermaid_light_theme = "neutral"
nb_execution_mode = "cache"
nb_execution_raise_on_error = True
nb_execution_timeout = 600
nb_execution_show_tb = True
nb_merge_streams = True

doctest_global_setup = "import numpy as np"

# -- HTML ---------------------------------------------------------------------

html_theme = "furo"
html_title = f"geodetic-engine {version}"
html_short_title = "geodetic-engine"
html_logo = "_static/logo.svg"
html_favicon = "_static/favicon.svg"
html_show_sourcelink = False
html_copy_source = False
html_theme_options = {
    "sidebar_hide_name": True,
    "navigation_with_keys": True,
    "top_of_page_buttons": ["view", "edit"],
    "source_repository": REPOSITORY,
    "source_branch": "main",
    "source_directory": "docs/",
    "announcement": (
        "Development documentation, built from <code>main</code> against "
        f"PROJ {pyproj.proj_version_str}. Every example on this site was executed "
        "during the build."
    ),
    "light_css_variables": {
        "color-brand-primary": "#0b6b86",
        "color-brand-content": "#0b6b86",
        "color-brand-visited": "#0b6b86",
        "color-announcement-background": "#0b6b86",
        "color-announcement-text": "#ffffff",
        "color-sidebar-background": "#f7f9fa",
        "color-sidebar-caption-text": "#0b6b86",
        "color-admonition-title--note": "#0b6b86",
        "color-admonition-title-background--note": "#0b6b861a",
        "font-stack": (
            '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
            '"Helvetica Neue", Arial, sans-serif'
        ),
        "font-stack--monospace": (
            '"JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace'
        ),
        "font-stack--headings": (
            '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
            '"Helvetica Neue", Arial, sans-serif'
        ),
    },
    "dark_css_variables": {
        "color-brand-primary": "#5cc0dd",
        "color-brand-content": "#5cc0dd",
        "color-brand-visited": "#5cc0dd",
        "color-announcement-background": "#0b3d4c",
        "color-announcement-text": "#e6f4f8",
        "color-sidebar-background": "#161b1f",
        "color-sidebar-caption-text": "#5cc0dd",
        "color-admonition-title--note": "#5cc0dd",
        "color-admonition-title-background--note": "#5cc0dd1a",
    },
    "footer_icons": [
        {
            "name": "GitHub",
            "url": REPOSITORY,
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0" '
                'viewBox="0 0 16 16"><path fill-rule="evenodd" d="M8 0C3.58 0 0 '
                "3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01"
                "-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13"
                "-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 "
                "2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31"
                "-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 "
                "1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 "
                "1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 "
                "3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55"
                '.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"></path></svg>'
            ),
            "class": "",
        },
    ],
}

pygments_style = "friendly"
pygments_dark_style = "monokai"

rst_prolog = f"""
.. |proj_version| replace:: {pyproj.proj_version_str}
.. |pyproj_version| replace:: {pyproj.__version__}
"""
myst_substitutions = {
    "proj_version": pyproj.proj_version_str,
    "pyproj_version": pyproj.__version__,
}

linkcheck_ignore = [
    # Placeholder hosts used in configuration examples.
    r"https://georepository\.example\.com.*",
    r"https://login\.example\.com.*",
]
