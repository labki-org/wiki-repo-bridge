"""Pure functions for building wiki page names from a Project + tag + component context.

Page tree convention:

    <Project>                                        # human-curated, bootstrapped once
    <Project>/Components/<Component name>            # canonical (un-versioned) Component family
    <Project>/Components/<Component name>/<version>  # immutable versioned snapshot
    <Project>/Releases/<version>                     # immutable per-tag manifest

Versions in page names use the bare semver string (``1.2.0``), not the git tag (``v1.2.0``).
Tags get stripped of a leading ``v`` here so ``v1.2.0`` and ``1.2.0`` both produce the same
Release page name.
"""

from __future__ import annotations


def normalize_version(version_or_tag: str) -> str:
    """Strip a leading ``v`` from a version-or-tag string.

    Both ``v1.2.0`` and ``1.2.0`` normalize to ``1.2.0``.
    """
    return version_or_tag[1:] if version_or_tag.startswith("v") else version_or_tag


def project_page(project: str) -> str:
    """The top-level human-curated Project page."""
    return project


def component_family_page(project: str, component_name: str) -> str:
    """The canonical un-versioned page for a component (e.g. ``MiniXL/Components/Housing``)."""
    return f"{project}/Components/{component_name}"


def versioned_component_page(project: str, component_name: str, version: str) -> str:
    """The immutable per-version Component page (e.g. ``MiniXL/Components/Housing/1.0.2``)."""
    return f"{project}/Components/{component_name}/{normalize_version(version)}"


def release_page(project: str, tag_or_version: str) -> str:
    """The per-tag immutable Release manifest page (e.g. ``MiniXL/Releases/1.2.0``)."""
    return f"{project}/Releases/{normalize_version(tag_or_version)}"
