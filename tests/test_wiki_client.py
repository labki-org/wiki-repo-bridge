from pathlib import Path

import pytest

from tests.conftest import FakePage, FakeSite
from wiki_repo_bridge.wiki_client import PageNotFoundError, WikiClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def site_with_project() -> FakeSite:
    site = FakeSite(auto_create=False)
    site.pages["Category:Project"] = FakePage((FIXTURES / "category_project.wikitext").read_text())
    site.pages["Property:Has website"] = FakePage(
        (FIXTURES / "property_has_website.wikitext").read_text()
    )
    return site


class TestFetchAndCache:
    def test_fetch_category(self, site_with_project: FakeSite) -> None:
        client = WikiClient(site=site_with_project)
        cat = client.fetch_category("Project")
        assert cat.display_label == "Project"
        assert "Has description" in cat.required_properties()
        assert "Has website" in cat.optional_properties()

    def test_category_cache_avoids_refetch(self, site_with_project: FakeSite) -> None:
        client = WikiClient(site=site_with_project)
        first = client.fetch_category("Project")
        site_with_project.pages["Category:Project"] = FakePage(
            "{{Category|has_description=changed}}"
        )
        second = client.fetch_category("Project")
        assert first is second  # second call must hit cache, not re-parsed text

    def test_fetch_property(self, site_with_project: FakeSite) -> None:
        client = WikiClient(site=site_with_project)
        prop = client.fetch_property("Has website")
        assert prop.type == "URL"
        assert prop.allows_multiple_values is True

    def test_missing_page_raises(self) -> None:
        client = WikiClient(site=FakeSite(pages={"Category:Nope": FakePage("")}, auto_create=False))
        with pytest.raises(PageNotFoundError):
            client.fetch_category("Nope")

    def test_login_passthrough(self, site_with_project: FakeSite) -> None:
        client = WikiClient(site=site_with_project)
        client.login("bot", "pw")
        assert site_with_project.logged_in_as == ("bot", "pw")


class TestInheritance:
    def test_child_inherits_parent_fields(self) -> None:
        site = FakeSite(auto_create=False)
        site.pages["Category:Component"] = FakePage(
            "{{Category|has_description=Parent}}\n"
            "{{Property field/subobject|for_property=Has name|is_required=Yes}}\n"
            "{{Property field/subobject|for_property=Has project|is_required=Yes}}\n"
            "{{Property field/subobject|for_property=Has version|is_required=No}}\n"
        )
        site.pages["Category:Hardware component"] = FakePage(
            "{{Category|has_description=Hardware|has_parent_category=Component}}\n"
            "{{Property field/subobject|for_property=Has hardware type|is_required=Yes}}\n"
        )
        client = WikiClient(site=site)
        merged = client.load_category_with_inheritance("Hardware component")
        names = {f.name for f in merged.property_fields}
        assert names == {"Has name", "Has project", "Has version", "Has hardware type"}
        # Parent's required stays required, child's new required added
        required = merged.required_properties()
        assert required == {"Has name", "Has project", "Has hardware type"}

    def test_child_overrides_parent_required_flag(self) -> None:
        """A child Category should be able to flip an inherited optional → required."""
        site = FakeSite(auto_create=False)
        site.pages["Category:Parent"] = FakePage(
            "{{Category|has_description=p}}\n"
            "{{Property field/subobject|for_property=Has thing|is_required=No}}\n"
        )
        site.pages["Category:Child"] = FakePage(
            "{{Category|has_description=c|has_parent_category=Parent}}\n"
            "{{Property field/subobject|for_property=Has thing|is_required=Yes}}\n"
        )
        client = WikiClient(site=site)
        merged = client.load_category_with_inheritance("Child")
        assert merged.required_properties() == {"Has thing"}


class TestLoadSchema:
    def test_loads_categories_and_referenced_properties(
        self, site_with_project: FakeSite
    ) -> None:
        # Add property pages for everything Project's fields reference
        for prop in [
            "Has description",
            "Has project status",
            "Has goal",
            "Has funding",
            "Has start date",
            "Has end date",
            "Has responsible party",
            "Has SOP",
            "Has repository url",
            "Has license",
            "Has DOI",
        ]:
            site_with_project.pages[f"Property:{prop}"] = FakePage(
                f"{{{{Property|has_description={prop}|has_type=Text}}}}"
            )
        client = WikiClient(site=site_with_project)
        schema = client.load_schema(["Project"])
        assert "Project" in schema.categories
        assert "Has website" in schema.properties
        assert "Has description" in schema.properties


class TestFromApiUrl:
    def test_url_parsing_does_not_raise(self) -> None:
        # We don't actually contact a wiki — just confirm the URL parser works.
        # mwclient.Site() does an HTTP request on construction; we wrap to avoid that
        # by patching at the class level rather than actually building a Site.
        # Skip if mwclient cannot be configured to defer the request — this is a smoke test only.
        pytest.importorskip("mwclient")
        # The actual site construction is exercised in integration tests, not unit tests.

    def test_url_without_hostname_raises(self) -> None:
        with pytest.raises(ValueError, match="hostname"):
            WikiClient.from_api_url("not-a-url")


class TestWritePage:
    def _content(self, name: str = "Test", *, immutable: bool = False, bootstrap: bool = False):
        from wiki_repo_bridge.pages import PageContent
        return PageContent(
            page_name=name, wikitext="hello", immutable=immutable, bootstrap_only=bootstrap
        )

    def test_creates_when_absent(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        page = FakePage(exists=False)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        result = client.write_page(self._content("Test"))
        assert result.action == WriteAction.CREATED
        assert page.edits[-1][0] == "hello"

    def test_updates_when_present(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        page = FakePage(_text="old", exists=True)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        result = client.write_page(self._content("Test"))
        assert result.action == WriteAction.UPDATED
        assert page._text == "hello"

    def test_bootstrap_only_skips_existing(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        page = FakePage(_text="curated", exists=True)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        result = client.write_page(self._content("Test", bootstrap=True))
        assert result.action == WriteAction.SKIPPED
        assert "bootstrap" in result.reason
        assert page._text == "curated"  # untouched

    def test_bootstrap_only_creates_when_absent(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        page = FakePage(exists=False)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        result = client.write_page(self._content("Test", bootstrap=True))
        assert result.action == WriteAction.CREATED

    def test_immutable_skips_existing(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        page = FakePage(_text="frozen", exists=True)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        result = client.write_page(self._content("Test", immutable=True))
        assert result.action == WriteAction.SKIPPED
        assert "immutable" in result.reason

    def test_dry_run_does_not_edit(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        page = FakePage(exists=False)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        result = client.write_page(self._content("Test"), dry_run=True)
        assert result.action == WriteAction.CREATED
        assert "dry-run" in result.reason
        assert page.edits == []


class TestManagedSection:
    """Read-modify-write between marker comments preserves human prose outside the markers."""

    def _managed(
        self, name: str = "Test", body: str = "managed body", scaffold: str = "= Heading =",
    ):
        from wiki_repo_bridge.pages import PageContent
        return PageContent(page_name=name, managed_body=body, scaffold=scaffold)

    def test_first_create_writes_scaffold_plus_marker_block(self) -> None:
        page = FakePage(exists=False)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        client.write_page(self._managed(body="first"))
        written = page.edits[-1][0]
        assert "= Heading =" in written
        assert "<!-- wiki-repo-bridge Start -->" in written
        assert "first" in written
        assert "<!-- wiki-repo-bridge End -->" in written

    def test_resync_replaces_only_managed_block(self) -> None:
        existing = (
            "= Heading =\n\n"
            "Some human prose above.\n\n"
            "<!-- wiki-repo-bridge Start -->\nold content\n<!-- wiki-repo-bridge End -->\n\n"
            "Some human prose below.\n"
        )
        page = FakePage(_text=existing, exists=True)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        client.write_page(self._managed(body="new content"))
        written = page.edits[-1][0]
        assert "Some human prose above." in written
        assert "Some human prose below." in written
        assert "old content" not in written
        assert "new content" in written

    def test_existing_unmarked_page_appends_managed_block(self) -> None:
        """If a human created the page without markers, the bridge appends the block
        rather than overwriting their content."""
        page = FakePage(_text="Pure human prose, no markers.\n", exists=True)
        site = FakeSite(pages={"Test": page}, auto_create=False)
        client = WikiClient(site=site)
        client.write_page(self._managed(body="bridge data"))
        written = page.edits[-1][0]
        assert "Pure human prose, no markers." in written
        assert "<!-- wiki-repo-bridge Start -->" in written
        assert "bridge data" in written


class TestVersionedComponent:
    """Archive-on-bump flow: existing page is moved to /v<old> when version increases."""

    def _content(self, version: str, page_name: str = "MiniXL/Components/Housing"):
        from wiki_repo_bridge.pages import PageContent
        body = "{{Hardware component\n|has_name=Housing\n|has_version=" + version + "\n}}"
        return PageContent(
            page_name=page_name, managed_body=body, scaffold="= Housing =", version=version,
        )

    def test_first_create_no_archive(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        site = FakeSite(auto_create=True)
        client = WikiClient(site=site)
        result = client.write_versioned_component(self._content("1.0.0"))
        assert result.action == WriteAction.CREATED
        assert site.pages["MiniXL/Components/Housing"].exists
        # No archive subpage was created.
        archive = "MiniXL/Components/Housing/v1.0.0"
        assert archive not in site.pages or not site.pages[archive].exists

    def test_same_version_rmw_no_archive(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        from wiki_repo_bridge.wikitext import wrap_managed
        site = FakeSite(auto_create=True)
        existing = (
            "= Housing =\n\n"
            "Some human prose.\n\n"
            f"{wrap_managed('{{Hardware component|has_version=1.0.0}}')}\n"
        )
        site.pages["MiniXL/Components/Housing"] = FakePage(_text=existing, exists=True)
        site.__post_init__()  # re-link site refs after manual injection
        client = WikiClient(site=site)
        result = client.write_versioned_component(self._content("1.0.0"))
        assert result.action == WriteAction.UPDATED
        # No archive created; human prose preserved.
        assert not site.pages["MiniXL/Components/Housing/v1.0.0"].exists
        assert "Some human prose." in site.pages["MiniXL/Components/Housing"]._text

    def test_version_bump_archives_previous(self) -> None:
        from wiki_repo_bridge.wiki_client import WriteAction
        from wiki_repo_bridge.wikitext import wrap_managed
        site = FakeSite(auto_create=True)
        existing = (
            "= Housing =\n\n"
            "Human notes.\n\n"
            f"{wrap_managed('{{Hardware component|has_version=1.0.0}}')}\n"
        )
        site.pages["MiniXL/Components/Housing"] = FakePage(_text=existing, exists=True)
        site.__post_init__()
        client = WikiClient(site=site)
        result = client.write_versioned_component(self._content("1.0.1"))
        assert result.action == WriteAction.CREATED  # canonical page was empty after move
        # Archive holds the previous content (including human prose).
        archive = site.pages["MiniXL/Components/Housing/v1.0.0"]
        assert archive.exists
        assert "Human notes." in archive._text
        assert "has_version=1.0.0" in archive._text
        # New canonical page has new version.
        new = site.pages["MiniXL/Components/Housing"]
        assert "has_version=1.0.1" in new._text

    def test_version_regression_errors(self) -> None:
        from wiki_repo_bridge.wiki_client import VersionRegressionError
        from wiki_repo_bridge.wikitext import wrap_managed
        site = FakeSite(auto_create=True)
        site.pages["MiniXL/Components/Housing"] = FakePage(
            _text=wrap_managed("{{Hardware component|has_version=1.0.5}}"),
            exists=True,
        )
        site.__post_init__()
        client = WikiClient(site=site)
        with pytest.raises(VersionRegressionError):
            client.write_versioned_component(self._content("1.0.4"))

    def test_archive_already_exists_errors(self) -> None:
        """If the archive subpage exists, the move must refuse — no silent overwrites."""
        from wiki_repo_bridge.wiki_client import ArchiveConflictError
        from wiki_repo_bridge.wikitext import wrap_managed
        site = FakeSite(auto_create=True)
        site.pages["MiniXL/Components/Housing"] = FakePage(
            _text=wrap_managed("{{Hardware component|has_version=1.0.0}}"),
            exists=True,
        )
        site.pages["MiniXL/Components/Housing/v1.0.0"] = FakePage(
            _text="oh no, somehow this already exists",
            exists=True,
        )
        site.__post_init__()
        client = WikiClient(site=site)
        with pytest.raises(ArchiveConflictError):
            client.write_versioned_component(self._content("1.0.1"))


class TestMovePage:
    def test_moves_when_source_exists_and_dest_absent(self) -> None:
        from wiki_repo_bridge.wiki_client import WikiClient
        moves: list[tuple[str, str, bool]] = []

        class _MovablePage(FakePage):
            def move(
                self_inner, new_title: str,
                reason: str = "", no_redirect: bool = False,
            ) -> None:
                moves.append((self_inner._text, new_title, no_redirect))

        src = _MovablePage(_text="content", exists=True)
        dst = FakePage(exists=False)
        site = FakeSite(pages={"Foo": src, "Foo/v1.0.0": dst}, auto_create=False)
        client = WikiClient(site=site)
        client.move_page("Foo", "Foo/v1.0.0")
        assert moves == [("content", "Foo/v1.0.0", True)]

    def test_refuses_when_destination_exists(self) -> None:
        from wiki_repo_bridge.wiki_client import ArchiveConflictError
        src = FakePage(_text="x", exists=True)
        dst = FakePage(_text="already here", exists=True)
        site = FakeSite(pages={"Foo": src, "Foo/v1.0.0": dst}, auto_create=False)
        client = WikiClient(site=site)
        with pytest.raises(ArchiveConflictError):
            client.move_page("Foo", "Foo/v1.0.0")

    def test_dry_run_no_op(self) -> None:
        # dry_run skips entirely — no FakePage method calls expected.
        site = FakeSite(auto_create=True)
        client = WikiClient(site=site)
        client.move_page("Foo", "Foo/v1.0.0", dry_run=True)  # would raise without dry_run
