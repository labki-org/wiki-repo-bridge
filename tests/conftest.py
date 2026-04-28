"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from wiki_repo_bridge.schema import (
    CategoryDef,
    PropertyDef,
    PropertyField,
    Schema,
)
from wiki_repo_bridge.walker import WikiYmlFile


def make_schema() -> Schema:
    """Build a Schema covering every Category and Property the test suite touches.

    Used by test_pages and test_sync; kept as a regular function rather than a
    pytest fixture so it can be called from helper code at module level.
    """
    schema = Schema()
    schema.categories["Project"] = CategoryDef(
        name="Project",
        property_fields=[
            PropertyField(name="Has description", required=True),
            PropertyField(name="Has project status", required=True),
            PropertyField(name="Has repository url", required=False),
            PropertyField(name="Has license", required=False),
            PropertyField(name="Has DOI", required=False),
            PropertyField(name="Has predecessor", required=False),
        ],
    )
    schema.categories["Hardware component"] = CategoryDef(
        name="Hardware component",
        property_fields=[
            PropertyField(name="Has name", required=True),
            PropertyField(name="Has project", required=True),
            PropertyField(name="Has version", required=False),
            PropertyField(name="Has description", required=False),
            PropertyField(name="Has hardware type", required=False),
            PropertyField(name="Has source path", required=False),
            PropertyField(name="Has design file url", required=False),
            PropertyField(name="Has release", required=False),
            PropertyField(name="Has image", required=False),
        ],
    )
    schema.categories["Release"] = CategoryDef(
        name="Release",
        property_fields=[
            PropertyField(name="Has name", required=True),
            PropertyField(name="Has version", required=True),
            PropertyField(name="Has release date", required=True),
            PropertyField(name="Has project", required=True),
            PropertyField(name="Has responsible party", required=True),
            PropertyField(name="Has tag", required=False),
            PropertyField(name="Has changelog", required=False),
            PropertyField(name="Has component", required=False),
            PropertyField(name="Has artifact url", required=False),
            PropertyField(name="Has image", required=False),
        ],
    )
    for prop in [
        "Has description", "Has project status", "Has repository url", "Has license",
        "Has DOI", "Has predecessor", "Has name", "Has project", "Has version",
        "Has hardware type", "Has source path", "Has design file url", "Has release",
        "Has release date", "Has tag", "Has changelog", "Has component",
        "Has artifact url", "Has responsible party", "Has image",
    ]:
        schema.properties[prop] = PropertyDef(name=prop, type="Text")
    return schema


@dataclass
class FakePage:
    """Stand-in for ``mwclient.page.Page`` — tracks writes for assertions."""

    _text: str = ""
    exists: bool = True
    edits: list[tuple[str, str]] = field(default_factory=list)

    def text(self) -> str:
        return self._text

    def edit(self, text: str, summary: str) -> None:
        self.edits.append((text, summary))
        self._text = text
        self.exists = True


@dataclass
class FakeSite:
    """Stand-in for ``mwclient.Site``. By default unknown page lookups return a
    non-existent FakePage, matching mwclient's behavior. ``login()`` sets
    ``username`` to mirror mwclient's post-login state."""

    pages: dict[str, FakePage] = field(default_factory=dict)
    logged_in_as: tuple[str, str] | None = None
    username: str | None = None
    auto_create: bool = True

    def __post_init__(self) -> None:
        if not self.auto_create:
            return
        original = self.pages

        class _AutoDict(dict):
            def __missing__(self_inner, key: str) -> FakePage:
                page = FakePage(exists=False)
                self_inner[key] = page
                return page

        ad = _AutoDict()
        ad.update(original)
        self.pages = ad

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = (username, password)
        self.username = username


def write_text(path: Path, content: str) -> None:
    """Create parent dirs and write ``content`` to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def make_wiki_yml_file(rel: str, content: dict) -> WikiYmlFile:
    """Construct a WikiYmlFile without touching the filesystem."""
    return WikiYmlFile(path=Path(f"/tmp/{rel}"), relative_path=Path(rel), content=content)
