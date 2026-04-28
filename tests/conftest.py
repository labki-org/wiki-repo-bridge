"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from wiki_repo_bridge.walker import WikiYmlFile


@dataclass
class FakePage:
    """Stand-in for ``mwclient.page.Page`` — tracks writes and moves for assertions.

    ``site`` and ``name`` are set by FakeSite when the page is first vended; tests
    that construct FakePages directly without going through a site can leave them
    None (move() requires both)."""

    _text: str = ""
    exists: bool = True
    edits: list[tuple[str, str]] = field(default_factory=list)
    site: FakeSite | None = None
    name: str | None = None
    moves: list[tuple[str, str, bool]] = field(default_factory=list)

    def text(self) -> str:
        return self._text

    def edit(self, text: str, summary: str) -> None:
        self.edits.append((text, summary))
        self._text = text
        self.exists = True

    def move(self, new_title: str, reason: str = "", no_redirect: bool = False) -> None:
        """Move this page, modeling the wiki-side outcome: the destination ends up
        with this page's text and exists=True; this page becomes empty + non-existent
        (or a redirect, when no_redirect=False — the bridge always uses no_redirect)."""
        self.moves.append((self._text, new_title, no_redirect))
        if self.site is not None:
            dest = self.site.pages[new_title]
            dest._text = self._text
            dest.exists = True
            # Mirror mwclient: source page is gone (or redirect — we model only no_redirect).
            self._text = ""
            self.exists = False


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
        # Backfill site/name on any pre-seeded pages so move() can reach the dict.
        for name, page in list(self.pages.items()):
            page.site = self
            page.name = name
        if not self.auto_create:
            return
        original = self.pages
        site_ref = self

        class _AutoDict(dict):
            def __missing__(self_inner, key: str) -> FakePage:
                page = FakePage(exists=False, site=site_ref, name=key)
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
