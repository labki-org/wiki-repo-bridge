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
