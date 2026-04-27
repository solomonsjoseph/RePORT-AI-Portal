"""Sphinx configuration for the active RePORT AI Portal documentation build."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Add the project root to the Python import path for autodoc and version import.
_DOCS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DOCS_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Import version from the canonical single source of truth.
from __version__ import __version__  # noqa: E402

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project: str = "RePORT AI Portal"
copyright: str = "2025–2026, Solomon S Joseph"
author: str = "Solomon S Joseph"
version: str = __version__
release: str = __version__

# Minimum supported Sphinx version for this documentation configuration.
needs_sphinx: str = "7.0"

# Global substitutions available in all .rst files.
rst_prolog: str = f"""
.. |version| replace:: {version}
.. |release| replace:: {release}
"""

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions: list[str] = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
    "sphinx_autodoc_typehints",
]

templates_path: list[str] = []
exclude_patterns: list[str] = ["_build", "Thumbs.db", ".DS_Store"]
language: str = "en"

# Napoleon settings for Google and NumPy style docstrings.
napoleon_google_docstring: bool = True
napoleon_numpy_docstring: bool = True
napoleon_include_init_with_doc: bool = True

# Show type hints in the rendered signature/description via the extension.
autodoc_typehints: str = "description"

# Developer mode controls inclusion of developer-only sections.
developer_mode: bool = os.environ.get("DEVELOPER_MODE", "True").lower() in {"true", "1", "yes"}
if not developer_mode:
    exclude_patterns.extend(["developer_guide/*", "api/*"])

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme: str = "sphinx_rtd_theme"
html_static_path: list[str] = []
html_theme_options: dict[str, Any] = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "includehidden": True,
    "prev_next_buttons_location": "bottom",
    "style_external_links": True,
}

html_context: dict[str, bool] = {
    "developer_mode": developer_mode,
}

# -- Options for intersphinx extension ---------------------------------------
# https://www.sphinx-doc.org/en/master/usage/extensions/intersphinx.html#configuration

intersphinx_mapping: dict[str, tuple[str, None]] = {
    "python": ("https://docs.python.org/3", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# -- Options for linkcheck builder -------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-the-linkcheck-builder
#
# HHS.gov and several Indian government hosts (abdm.gov.in, icmr.nic.in)
# return 403 to automated linkcheck user-agents or fail DNS intermittently,
# but the URLs resolve correctly in a browser. Ignore those specific hosts
# rather than treating them as broken links. See
# https://github.com/sphinx-doc/sphinx/issues/11434 for the broader class of
# "bot-blocked but valid" links.
linkcheck_ignore: list[str] = [
    r"^https://www\.hhs\.gov/hipaa/.*",
    r"^https://www\.hhs\.gov/ohrp/.*",
    r"^https://abdm\.gov\.in/.*",
    r"^https://main\.icmr\.nic\.in/.*",
    r"^https://dl\.acm\.org/doi/.*",
]
