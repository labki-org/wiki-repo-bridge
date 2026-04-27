from pathlib import Path

import pytest

from wiki_repo_bridge.wiki_parser import parse_category, parse_property

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestParseProperty:
    def test_has_website(self) -> None:
        prop = parse_property(_read("property_has_website.wikitext"), "Has website")
        assert prop.name == "Has website"
        assert prop.description == "Website"
        assert prop.type == "URL"
        assert prop.display_label == "Website"
        assert prop.allows_multiple_values is True
        assert prop.allows_value == []
        assert prop.allows_value_from_category is None

    def test_no_template_raises(self) -> None:
        with pytest.raises(ValueError, match=r"No Property data found"):
            parse_property("nothing here", "Has nothing")

    def test_raw_smw_form(self) -> None:
        """Bootstrap properties on the wiki use raw [[Has X::Y]] annotations
        rather than the {{Property|...}} dispatcher template."""
        wikitext = (
            "<!-- SemanticSchemas Start -->\n"
            "[[Has type::Text]]\n"
            "[[Has description::Explains the purpose of this category, property, or subobject.]]\n"
            "[[Display label::Description]]\n"
            "[[Has input type::textarea]]\n"
            "<!-- SemanticSchemas End -->\n"
            "[[Category:SemanticSchemas-managed-property]]\n"
        )
        prop = parse_property(wikitext, "Has description")
        assert prop.type == "Text"
        assert prop.display_label == "Description"
        assert "Explains the purpose" in (prop.description or "")
        assert prop.allows_multiple_values is False


class TestParseCategoryWikiForm:
    def test_project_metadata(self) -> None:
        cat = parse_category(_read("category_project.wikitext"), "Project")
        assert cat.name == "Project"
        assert cat.display_label == "Project"
        assert "multi-component effort" in (cat.description or "")
        assert cat.show_backlinks_for == "Has project"
        assert cat.parent_category is None

    def test_project_required_fields(self) -> None:
        cat = parse_category(_read("category_project.wikitext"), "Project")
        required = cat.required_properties()
        assert required == {"Has description", "Has project status"}

    def test_project_optional_fields_include_known(self) -> None:
        cat = parse_category(_read("category_project.wikitext"), "Project")
        optional = cat.optional_properties()
        for expected in [
            "Has goal",
            "Has funding",
            "Has start date",
            "Has end date",
            "Has responsible party",
            "Has SOP",
            "Has repository url",
            "Has license",
            "Has DOI",
            "Has website",
        ]:
            assert expected in optional, f"{expected!r} missing from optional properties"

    def test_project_has_no_subobject_fields(self) -> None:
        cat = parse_category(_read("category_project.wikitext"), "Project")
        assert cat.subobject_fields == []


class TestParseCategoryCompactForm:
    def test_compact_required_and_optional(self) -> None:
        cat = parse_category(_read("category_compact_form.wikitext"), "Project")
        assert cat.required_properties() == {"Has description", "Has status", "Has PI"}
        for expected in ["Has goal", "Has funding", "Has predecessor", "Has successor"]:
            assert expected in cat.optional_properties()

    def test_compact_subobjects(self) -> None:
        cat = parse_category(_read("category_compact_form.wikitext"), "Project")
        assert len(cat.subobject_fields) == 1
        assert cat.subobject_fields[0].target_category == "Has project role"
        assert cat.subobject_fields[0].required is False
