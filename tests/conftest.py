"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from wiki_repo_bridge.walker import WikiYmlFile


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
    non-existent FakePage, matching mwclient's behavior."""

    pages: dict[str, FakePage] = field(default_factory=dict)
    logged_in_as: tuple[str, str] | None = None
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


def write_text(path: Path, content: str) -> None:
    """Create parent dirs and write ``content`` to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def make_wiki_yml_file(rel: str, content: dict) -> WikiYmlFile:
    """Construct a WikiYmlFile without touching the filesystem."""
    return WikiYmlFile(path=Path(f"/tmp/{rel}"), relative_path=Path(rel), content=content)
