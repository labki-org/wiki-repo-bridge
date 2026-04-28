"""Pure functions for building wiki page names from a Project + tag + component context.

Page tree convention:

    <Project>                                            # managed; humans curate prose
    <Project>/Components/<Component>                     # managed; latest version
    <Project>/Components/<Component>/v<old version>      # archive subpage from prior bumps
    <Project>/Releases/<version>                         # immutable per-tag manifest

Archive subpages are created by *moving* the current Component page to ``/v<old>`` when
a new version is synced. They are never rendered fresh — they preserve the moved page's
full edit history.

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
    """The top-level Project page."""
    return project


def component_page(project: str, component_name: str) -> str:
    """The canonical Component page — the latest version (``MiniXL/Components/Housing``)."""
    return f"{project}/Components/{component_name}"


def component_archive_page(project: str, component_name: str, version: str) -> str:
    """The archive subpage for a previous version (e.g. ``MiniXL/Components/Housing/v1.0.0``).

    Created by moving the Component page on a version bump; the leading ``v`` distinguishes
    archive subpages from any future non-version subpages a wiki author might add.
    """
    return f"{project}/Components/{component_name}/v{normalize_version(version)}"


def release_page(project: str, tag_or_version: str) -> str:
    """The per-tag immutable Release manifest page (e.g. ``MiniXL/Releases/1.2.0``)."""
    return f"{project}/Releases/{normalize_version(tag_or_version)}"
