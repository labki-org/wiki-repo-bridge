"""Pure functions for building wiki page names from a Project + tag + component context.

Page tree convention:

    <Project>                                          # managed; humans curate prose
    <Project>/Component/<Component>                    # managed; latest version
    <Project>/Component/<Component>/v<version>         # immutable per-version snapshot
    <Project>/Release/<version>                        # immutable per-tag manifest

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
    """The canonical Component page — the latest version (``MiniXL/Component/Housing``)."""
    return f"{project}/Component/{component_name}"


def component_archive_page(project: str, component_name: str, version: str) -> str:
    """Per-version snapshot subpage (e.g. ``MiniXL/Component/Housing/v1.0.0``).

    Immutable. The leading ``v`` distinguishes version archives from any future
    non-version subpages a wiki author might add.
    """
    return f"{project}/Component/{component_name}/v{normalize_version(version)}"


def release_page(project: str, tag_or_version: str) -> str:
    """The per-tag immutable Release manifest page (e.g. ``MiniXL/Release/1.2.0``)."""
    return f"{project}/Release/{normalize_version(tag_or_version)}"


def repo_blob_url(repository_url: str, tag: str, rel_path: str) -> str:
    """A GitHub blob URL pinned to a tag (e.g. ``.../blob/v0.1.0/baseplate/file.f3d``)."""
    return f"{repository_url.rstrip('/')}/blob/{tag}/{rel_path.lstrip('/')}"


def repo_tree_url(repository_url: str, tag: str, rel_path: str = "") -> str:
    """A GitHub tree URL pinned to a tag (e.g. ``.../tree/v0.1.0/baseplate``).

    With ``rel_path=""`` returns the repo root at that tag — the canonical "release artifact"
    URL surfaced as ``Has artifact url`` on Release pages.
    """
    base = f"{repository_url.rstrip('/')}/tree/{tag}"
    return f"{base}/{rel_path.lstrip('/')}" if rel_path else base
