"""Walk a repository tree to discover ``wiki.yml`` files and parse them into
typed records the validator/writer can consume.

The repo's top-level ``wiki.yml`` declares the Project; per-component-subdirectory
``wiki.yml`` files declare the components. Each file maps 1:1 to a wiki page that
the bridge will write under the project's subtree on each tagged release.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class WikiYmlFile:
    """One ``wiki.yml`` file located in a repository.

    ``relative_path`` is the path to the file from the repo root, useful for both
    constructing wiki page names and for human-readable error messages. ``content``
    is the fully parsed YAML mapping.
    """

    path: Path
    relative_path: Path
    content: dict[str, Any]

    @property
    def kind(self) -> str | None:
        """The declared ``kind`` field — e.g. ``project``, ``hardware_component``."""
        kind = self.content.get("kind")
        return str(kind) if kind is not None else None

    @property
    def directory(self) -> Path:
        """The directory containing this file (a component's source path, or the repo root)."""
        return self.path.parent


class WikiYmlError(Exception):
    """Raised when a ``wiki.yml`` file is missing, malformed, or violates a structural rule."""


def find_wiki_yml_files(repo_path: Path | str) -> list[WikiYmlFile]:
    """Return every ``wiki.yml`` file under ``repo_path``, parsed and ready to validate.

    The list is sorted with the root file first, then lexicographically by path so callers
    get a deterministic order. Hidden directories (``.git``, ``.venv``, ``node_modules``,
    etc.) and common build directories are skipped to keep walks fast on real repos.
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise WikiYmlError(f"{repo} is not a directory")

    skip_names = {".git", ".github", ".venv", "venv", "env", "node_modules", "__pycache__",
                  ".pytest_cache", ".ruff_cache", "build", "dist"}
    found: list[WikiYmlFile] = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            if entry.name in skip_names:
                continue
            if entry.is_dir():
                walk(entry)
            elif entry.is_file() and entry.name == "wiki.yml":
                rel = entry.relative_to(repo)
                content = _load_yaml(entry)
                found.append(WikiYmlFile(path=entry, relative_path=rel, content=content))

    walk(repo)
    found.sort(key=lambda f: (len(f.relative_path.parts), str(f.relative_path)))
    return found


def find_project_file(files: list[WikiYmlFile]) -> WikiYmlFile:
    """Return the single ``kind: project`` ``wiki.yml`` from a walk result.

    Raises :class:`WikiYmlError` if there is zero or more than one — the bridge writes
    one Project page per repo, so this should always be exactly one.
    """
    projects = [f for f in files if f.kind == "project"]
    if not projects:
        raise WikiYmlError("No wiki.yml with kind: project found in repo")
    if len(projects) > 1:
        paths = ", ".join(str(f.relative_path) for f in projects)
        raise WikiYmlError(f"Multiple kind: project wiki.yml files: {paths}")
    return projects[0]


def find_component_files(files: list[WikiYmlFile]) -> list[WikiYmlFile]:
    """Return all ``wiki.yml`` files declaring a component (any kind ending in ``_component``)."""
    return [f for f in files if (f.kind or "").endswith("_component")]


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise WikiYmlError(f"Could not parse YAML at {path}: {e}") from e
    if loaded is None:
        raise WikiYmlError(f"{path} is empty")
    if not isinstance(loaded, dict):
        kind = type(loaded).__name__
        raise WikiYmlError(f"{path} must be a YAML mapping at top level, got {kind}")
    return loaded
