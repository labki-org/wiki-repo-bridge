"""MediaWiki API client — fetch Category/Property page wikitext and build a Schema
by resolving parent-Category inheritance.

Wraps :mod:`mwclient` so the rest of the bridge doesn't depend on its surface directly,
which keeps tests easy to write with a mocked Site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import mwclient

from wiki_repo_bridge.pages import PageContent
from wiki_repo_bridge.schema import CategoryDef, PropertyDef, Schema
from wiki_repo_bridge.wiki_parser import parse_category, parse_property


class WriteAction(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class WriteResult:
    page_name: str
    action: WriteAction
    reason: str = ""

    def __str__(self) -> str:
        suffix = f" ({self.reason})" if self.reason else ""
        return f"[{self.action.value}] {self.page_name}{suffix}"


@dataclass
class WikiClient:
    """High-level access to a MediaWiki + SemanticSchemas wiki.

    ``site`` is duck-typed against ``mwclient.Site`` — production uses a real Site;
    tests pass a fake with the same shape (``site.pages[name].text()`` and
    ``site.pages[name].edit(text, summary)``).
    """

    site: Any
    user_agent: str = "wiki-repo-bridge/0.1 (+https://github.com/labki-org/wiki-repo-bridge)"
    _category_cache: dict[str, CategoryDef] = field(default_factory=dict, init=False, repr=False)
    _property_cache: dict[str, PropertyDef] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def from_api_url(cls, api_url: str, **kwargs) -> WikiClient:
        """Construct a client from a MediaWiki API URL like ``https://wiki.example.org/w/api.php``."""
        parsed = urlparse(api_url)
        if not parsed.hostname:
            raise ValueError(f"Could not parse hostname from {api_url!r}")
        path = parsed.path.removesuffix("api.php").rstrip("/") + "/"
        site = mwclient.Site(
            host=parsed.hostname,
            scheme=parsed.scheme or "https",
            path=path,
            clients_useragent=kwargs.pop(
                "user_agent", "wiki-repo-bridge/0.1 (+https://github.com/labki-org/wiki-repo-bridge)"
            ),
        )
        return cls(site=site, **kwargs)

    def login(self, username: str, password: str) -> None:
        """Authenticate to the wiki, raising :class:`WikiAuthError` if no session results.

        mwclient.Site.login() raises on outright failure but in some edge cases (e.g.
        certain mediawiki+bot-password combinations) returns success without actually
        establishing a session. Verifying ``site.username`` afterwards catches that.
        """
        self.site.login(username, password)
        actual = getattr(self.site, "username", None)
        if not actual:
            raise WikiAuthError(
                f"Login as {username!r} returned no error but did not establish a "
                "session — verify the bot username and password are correct (and that "
                "the username includes the @BotName suffix for bot-password logins)."
            )

    def fetch_wikitext(self, page_name: str) -> str:
        """Return the current wikitext of ``page_name`` (e.g. ``Category:Project``)."""
        try:
            page = self.site.pages[page_name]
        except Exception as e:  # mwclient.errors.APIError or similar
            if "readapidenied" in str(e):
                raise WikiAuthError(
                    "Wiki requires authentication for read access — "
                    "pass --bot-user and --bot-password (or set WIKI_REPO_BOT_USER / "
                    "WIKI_REPO_BOT_PASSWORD env vars)."
                ) from e
            raise
        exists = getattr(page, "exists", None)  # mocks may omit this attribute
        if exists is False:
            raise PageNotFoundError(f"Page {page_name!r} does not exist")
        text = page.text()
        if not text or not text.strip():
            raise PageNotFoundError(f"Page {page_name!r} exists but has empty wikitext")
        return text

    def fetch_category(self, name: str) -> CategoryDef:
        """Fetch and parse a Category page. Cached."""
        if name in self._category_cache:
            return self._category_cache[name]
        wikitext = self.fetch_wikitext(f"Category:{name}")
        cat = parse_category(wikitext, name)
        self._category_cache[name] = cat
        return cat

    def fetch_property(self, name: str) -> PropertyDef:
        """Fetch and parse a Property page. Cached."""
        if name in self._property_cache:
            return self._property_cache[name]
        wikitext = self.fetch_wikitext(f"Property:{name}")
        prop = parse_property(wikitext, name)
        self._property_cache[name] = prop
        return prop

    def load_category_with_inheritance(self, name: str) -> CategoryDef:
        """Fetch a Category and merge inherited property/subobject fields from its parent chain.

        Returns a *new* CategoryDef whose ``property_fields`` and ``subobject_fields`` are the
        union of this Category's and all ancestors'. A field declared on the child overrides
        the same field name from a parent (so a child can flip optional → required).
        """
        chain: list[CategoryDef] = []
        current = self.fetch_category(name)
        chain.append(current)
        while current.parent_category:
            parent = self.fetch_category(current.parent_category)
            chain.append(parent)
            current = parent

        # Walk parents → child so child fields win on name collision.
        prop_by_name: dict[str, _MergedField] = {}
        sub_by_name: dict[str, _MergedSub] = {}
        for layer in reversed(chain):
            for f in layer.property_fields:
                prop_by_name[f.name] = _MergedField(f.name, f.required)
            for s in layer.subobject_fields:
                sub_by_name[s.target_category] = _MergedSub(s.target_category, s.required)

        merged = CategoryDef(
            name=chain[0].name,
            description=chain[0].description,
            display_label=chain[0].display_label,
            parent_category=chain[0].parent_category,
            show_backlinks_for=chain[0].show_backlinks_for,
            target_namespace=chain[0].target_namespace,
        )
        for f in prop_by_name.values():
            from wiki_repo_bridge.schema import PropertyField

            merged.property_fields.append(PropertyField(name=f.name, required=f.required))
        for s in sub_by_name.values():
            from wiki_repo_bridge.schema import SubobjectField

            merged.subobject_fields.append(
                SubobjectField(target_category=s.target_category, required=s.required)
            )
        return merged

    def write_page(
        self,
        content: PageContent,
        *,
        edit_summary: str = "wiki-repo-bridge sync",
        dry_run: bool = False,
    ) -> WriteResult:
        """Write a :class:`PageContent` to the wiki, honoring its immutability flags.

        - ``bootstrap_only=True``: skip if the page already exists. Otherwise create.
        - ``immutable=True``: skip if the page already exists. Otherwise create.
        - default: create or update (overwrite existing wikitext).

        ``dry_run=True`` returns the would-be action without contacting the wiki.
        """
        page = self.site.pages[content.page_name]
        exists = bool(getattr(page, "exists", False))

        if content.bootstrap_only and exists:
            return WriteResult(
                content.page_name,
                WriteAction.SKIPPED,
                "bootstrap-only and page already exists",
            )
        if content.immutable and exists:
            return WriteResult(
                content.page_name,
                WriteAction.SKIPPED,
                "immutable and page already exists",
            )

        action = WriteAction.UPDATED if exists else WriteAction.CREATED
        reason = "dry-run" if dry_run else ""
        if not dry_run:
            page.edit(text=content.wikitext, summary=edit_summary)
        return WriteResult(content.page_name, action, reason)

    def load_schema(
        self, category_names: list[str], property_names: list[str] | None = None
    ) -> Schema:
        """Load a :class:`Schema` containing the requested Categories (with inheritance resolved)
        and any Properties referenced by their fields, plus any extra Properties named explicitly.
        """
        schema = Schema()
        for name in category_names:
            schema.categories[name] = self.load_category_with_inheritance(name)

        names_to_fetch: set[str] = set(property_names or [])
        for cat in schema.categories.values():
            for f in cat.property_fields:
                names_to_fetch.add(f.name)
        for name in names_to_fetch:
            schema.properties[name] = self.fetch_property(name)
        return schema


@dataclass(frozen=True)
class _MergedField:
    name: str
    required: bool


@dataclass(frozen=True)
class _MergedSub:
    target_category: str
    required: bool


class PageNotFoundError(Exception):
    """Raised when a requested page is empty or missing."""


class WikiAuthError(Exception):
    """Raised when the wiki rejects an unauthenticated request (private wiki without bot creds)."""
