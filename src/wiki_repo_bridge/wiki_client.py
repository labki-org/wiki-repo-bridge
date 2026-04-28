"""MediaWiki API client — fetch Category/Property page wikitext and build a Schema
by resolving parent-Category inheritance.

Wraps :mod:`mwclient` so the rest of the bridge doesn't depend on its surface directly,
which keeps tests easy to write with a mocked Site.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import mwclient

from wiki_repo_bridge.pages import PageContent
from wiki_repo_bridge.schema import CategoryDef, PropertyDef, Schema
from wiki_repo_bridge.wiki_parser import parse_category, parse_property
from wiki_repo_bridge.wikitext import (
    has_managed_block,
    parse_managed_version,
    replace_managed_block,
    semver_tuple,
    wrap_managed,
)

log = logging.getLogger(__name__)


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
        log.info("Logging in as %s", username)
        self.site.login(username, password)
        actual = getattr(self.site, "username", None)
        if not actual:
            raise WikiAuthError(
                f"Login as {username!r} returned no error but did not establish a "
                "session — verify the bot username and password are correct (and that "
                "the username includes the @BotName suffix for bot-password logins)."
            )
        log.info("Logged in as %s", actual)

    def fetch_wikitext(self, page_name: str) -> str:
        """Return the current wikitext of ``page_name`` (e.g. ``Category:Project``)."""
        try:
            page = self.site.pages[page_name]
        except mwclient.errors.APIError as e:
            if getattr(e, "code", None) == "readapidenied":
                raise WikiAuthError(
                    "Wiki requires authentication for read access — "
                    "pass --bot-user and --bot-password (or set WIKI_REPO_BOT_USER / "
                    "WIKI_REPO_BOT_PASSWORD env vars)."
                ) from e
            raise
        if not page.exists:
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
        """Write a :class:`PageContent` to the wiki, honoring its write mode.

        - ``managed_body`` set: read-modify-write between markers. On first create,
          writes ``scaffold`` + a wrapped managed block. On subsequent syncs, finds
          the existing markers and replaces only the content between them — so any
          human prose outside the markers is preserved.
        - ``bootstrap_only=True``: skip if the page already exists.
        - ``immutable=True``: skip if the page already exists.
        - default: overwrite existing wikitext with ``content.wikitext``.

        ``dry_run=True`` returns the would-be action without contacting the wiki.
        """
        page = self.site.pages[content.page_name]
        exists = page.exists

        if content.bootstrap_only and exists:
            return WriteResult(
                content.page_name, WriteAction.SKIPPED, "bootstrap-only and page already exists",
            )
        if content.immutable and exists:
            return WriteResult(
                content.page_name, WriteAction.SKIPPED, "immutable and page already exists",
            )

        new_text = self._compose_text(content, page, exists)
        action = WriteAction.UPDATED if exists else WriteAction.CREATED
        reason = "dry-run" if dry_run else ""
        if not dry_run:
            page.edit(text=new_text, summary=edit_summary)
        log.info("[%s] %s%s", action.value, content.page_name, f" ({reason})" if reason else "")
        return WriteResult(content.page_name, action, reason)

    @staticmethod
    def _compose_text(content: PageContent, page: Any, exists: bool) -> str:
        """Build the wikitext to write. Handles RMW for managed-body mode."""
        if content.managed_body is None:
            return content.wikitext
        if exists:
            existing = page.text() or ""
            if has_managed_block(existing):
                return replace_managed_block(existing, content.managed_body)
            # Page was created by a human (or pre-bridge tooling) without markers.
            # Preserve their prose by appending the managed block at the end.
            sep = "" if existing.endswith("\n") else "\n"
            return f"{existing}{sep}\n{wrap_managed(content.managed_body)}\n"
        # First create: scaffold above markers, managed block below.
        scaffold = content.scaffold.rstrip()
        prefix = f"{scaffold}\n\n" if scaffold else ""
        return f"{prefix}{wrap_managed(content.managed_body)}\n"

    def write_versioned_component(
        self,
        content: PageContent,
        *,
        edit_summary: str = "wiki-repo-bridge sync",
        dry_run: bool = False,
    ) -> WriteResult:
        """Write a versioned managed Component page, archiving the previous version on a bump.

        Branches on the comparison of ``content.version`` to the version found in the
        existing page's managed block:

        - page absent → create fresh
        - same version → RMW the managed block (mid-version wiki.yml edits propagate)
        - new > old   → move existing page to ``/v<old>``, then create fresh
        - new < old   → :class:`VersionRegressionError` (refuse to publish older content)

        Falls back to plain ``write_page`` semantics when ``content.version`` is unset.
        """
        if content.version is None:
            return self.write_page(content, edit_summary=edit_summary, dry_run=dry_run)

        page = self.site.pages[content.page_name]
        if not page.exists:
            return self.write_page(content, edit_summary=edit_summary, dry_run=dry_run)

        existing = page.text() or ""
        old_version = parse_managed_version(existing)

        # If the page exists but has no parseable version (pre-bridge or hand-curated),
        # fall through to RMW — write_page's append-managed-block path handles it.
        if old_version is None:
            return self.write_page(content, edit_summary=edit_summary, dry_run=dry_run)

        try:
            old = semver_tuple(old_version)
            new = semver_tuple(content.version)
        except ValueError:
            return self.write_page(content, edit_summary=edit_summary, dry_run=dry_run)

        if new == old:
            return self.write_page(content, edit_summary=edit_summary, dry_run=dry_run)
        if new < old:
            raise VersionRegressionError(
                f"Refusing to overwrite {content.page_name!r} (v{old_version}) "
                f"with older v{content.version}"
            )

        # Bump: move existing → archive subpage, then create fresh at the canonical name.
        archive_name = f"{content.page_name}/v{old_version}"
        log.info("Version bump %s → %s; archiving previous to %s",
                 old_version, content.version, archive_name)
        self.move_page(
            content.page_name, archive_name,
            reason=f"wiki-repo-bridge archive v{old_version}", dry_run=dry_run,
        )
        if dry_run:
            return WriteResult(content.page_name, WriteAction.CREATED, "dry-run (archive+create)")
        return self.write_page(content, edit_summary=edit_summary, dry_run=False)

    def upload_file(
        self,
        abs_path: Any,
        wiki_name: str,
        *,
        description: str = "wiki-repo-bridge image upload",
        dry_run: bool = False,
    ) -> str:
        """Upload (or refresh) a binary file as ``File:<wiki_name>``.

        Skips the upload when the wiki already has an identically-named file with a
        matching SHA-1 — saves bandwidth and avoids no-op revisions on each release.

        Returns one of ``"created"`` / ``"updated"`` / ``"skipped"`` / ``"dry-run"``.
        """
        from pathlib import Path

        import mwclient.errors

        from wiki_repo_bridge.images import file_sha1

        path = Path(abs_path)
        if dry_run:
            return "dry-run"

        local_sha1 = file_sha1(path)
        image = self.site.images[wiki_name]
        try:
            existed = image.exists
        except mwclient.errors.APIError as e:
            log.warning("Couldn't check existing %s on wiki: %s — uploading anyway", wiki_name, e)
            existed = False
        if existed and (image.imageinfo or {}).get("sha1") == local_sha1:
            log.info("[skipped] File:%s (sha1 matches)", wiki_name)
            return "skipped"

        with open(path, "rb") as f:
            self.site.upload(f, filename=wiki_name, description=description, ignore=True)
        action = "updated" if existed else "created"
        log.info("[%s] File:%s (%d bytes)", action, wiki_name, path.stat().st_size)
        return action

    def move_page(
        self,
        from_name: str,
        to_name: str,
        *,
        reason: str = "wiki-repo-bridge archive",
        no_redirect: bool = True,
        dry_run: bool = False,
    ) -> None:
        """Move a page to ``to_name``, suppressing the redirect by default.

        Raises if the source doesn't exist or the destination already exists; both
        states indicate inconsistency the bridge shouldn't paper over.
        """
        if dry_run:
            return
        source = self.site.pages[from_name]
        if not source.exists:
            raise PageNotFoundError(f"Cannot move missing page {from_name!r}")
        if self.site.pages[to_name].exists:
            raise ArchiveConflictError(
                f"Cannot move {from_name!r} → {to_name!r}: destination already exists"
            )
        log.info("Moving %s → %s", from_name, to_name)
        source.move(to_name, reason=reason, no_redirect=no_redirect)

    def load_schema(
        self, category_names: list[str], property_names: list[str] | None = None
    ) -> Schema:
        """Load a :class:`Schema` containing the requested Categories (with inheritance resolved)
        and any Properties referenced by their fields, plus any extra Properties named explicitly.
        """
        log.info("Fetching schema (%d categories) from wiki", len(category_names))
        schema = Schema()
        for name in category_names:
            schema.categories[name] = self.load_category_with_inheritance(name)

        names_to_fetch: set[str] = set(property_names or [])
        for cat in schema.categories.values():
            for f in cat.property_fields:
                names_to_fetch.add(f.name)
        log.info("Fetching %d properties from wiki", len(names_to_fetch))
        for name in names_to_fetch:
            schema.properties[name] = self.fetch_property(name)
        log.info("Schema loaded: %d categories, %d properties",
                 len(schema.categories), len(schema.properties))
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


class ArchiveConflictError(Exception):
    """Raised when a version-bump archive subpage already exists at the move destination."""


class VersionRegressionError(Exception):
    """Raised when a sync would replace a Component page with an older version."""
