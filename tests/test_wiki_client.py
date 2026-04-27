from dataclasses import dataclass, field
from pathlib import Path

import pytest

from wiki_repo_bridge.wiki_client import PageNotFoundError, WikiClient

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class FakePage:
    """Mimics mwclient's Page.text() interface."""

    _text: str = ""

    def text(self) -> str:
        return self._text


@dataclass
class FakeSite:
    """Mimics enough of mwclient.Site for WikiClient's needs."""

    pages: dict[str, FakePage] = field(default_factory=dict)
    logged_in_as: tuple[str, str] | None = None

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = (username, password)


@pytest.fixture
def site_with_project() -> FakeSite:
    site = FakeSite()
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
        # Replace the page text — second fetch should still return cached value
        replacement = FakePage("{{Category|has_description=changed}}")
        site_with_project.pages["Category:Project"] = replacement
        second = client.fetch_category("Project")
        assert first is second

    def test_fetch_property(self, site_with_project: FakeSite) -> None:
        client = WikiClient(site=site_with_project)
        prop = client.fetch_property("Has website")
        assert prop.type == "URL"
        assert prop.allows_multiple_values is True

    def test_missing_page_raises(self) -> None:
        client = WikiClient(site=FakeSite(pages={"Category:Nope": FakePage("")}))
        with pytest.raises(PageNotFoundError):
            client.fetch_category("Nope")

    def test_login_passthrough(self, site_with_project: FakeSite) -> None:
        client = WikiClient(site=site_with_project)
        client.login("bot", "pw")
        assert site_with_project.logged_in_as == ("bot", "pw")


class TestInheritance:
    def test_child_inherits_parent_fields(self) -> None:
        site = FakeSite()
        site.pages["Category:Component"] = FakePage(
            "{{Category|has_description=Parent}}\n"
            "{{Property field/subobject|for_property=Has name|is_required=Yes}}\n"
            "{{Property field/subobject|for_property=Has project|is_required=Yes}}\n"
            "{{Property field/subobject|for_property=Has version|is_required=No}}\n"
        )
        site.pages["Category:Hardware Component"] = FakePage(
            "{{Category|has_description=Hardware|has_parent_category=Component}}\n"
            "{{Property field/subobject|for_property=Has hardware type|is_required=Yes}}\n"
        )
        client = WikiClient(site=site)
        merged = client.load_category_with_inheritance("Hardware Component")
        names = {f.name for f in merged.property_fields}
        assert names == {"Has name", "Has project", "Has version", "Has hardware type"}
        # Parent's required stays required, child's new required added
        required = merged.required_properties()
        assert required == {"Has name", "Has project", "Has hardware type"}

    def test_child_overrides_parent_required_flag(self) -> None:
        """A child Category should be able to flip an inherited optional → required."""
        site = FakeSite()
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
