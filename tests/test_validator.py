from tests.conftest import make_wiki_yml_file
from wiki_repo_bridge.schema import (
    CategoryDef,
    PropertyDef,
    PropertyField,
    Schema,
)
from wiki_repo_bridge.validator import (
    Severity,
    has_errors,
    kind_to_category_name,
    validate_file,
    validate_files,
    yaml_key_to_property_name,
)


def make_schema() -> Schema:
    """A small schema mirroring the relevant Project + Hardware component shape."""
    schema = Schema()
    schema.categories["Project"] = CategoryDef(
        name="Project",
        property_fields=[
            PropertyField(name="Has description", required=True),
            PropertyField(name="Has project status", required=True),
            PropertyField(name="Has goal", required=False),
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
            PropertyField(name="Has hardware type", required=False),
            PropertyField(name="Has source path", required=False),
        ],
    )
    for prop_name in [
        "Has description", "Has project status", "Has goal", "Has repository url",
        "Has license", "Has DOI", "Has predecessor", "Has name", "Has project",
        "Has version", "Has hardware type", "Has source path",
    ]:
        schema.properties[prop_name] = PropertyDef(name=prop_name, type="Text")
    return schema


file_from_content = make_wiki_yml_file


class TestKindMapping:
    def test_simple(self) -> None:
        assert kind_to_category_name("project") == "Project"

    def test_compound(self) -> None:
        assert kind_to_category_name("hardware_component") == "Hardware component"

    def test_three_words(self) -> None:
        assert kind_to_category_name("analysis_component_thing") == "Analysis component thing"


class TestKeyMapping:
    def test_simple(self) -> None:
        assert yaml_key_to_property_name("description") == "Has description"

    def test_underscored(self) -> None:
        assert yaml_key_to_property_name("repository_url") == "Has repository url"


class TestValidateFile:
    def test_clean_project_file(self) -> None:
        schema = make_schema()
        f = file_from_content(
            "wiki.yml",
            {
                "kind": "project",
                "description": "x",
                "project_status": "active",
                "repository_url": "https://example.org",
            },
        )
        issues = validate_file(f, schema)
        assert issues == []

    def test_missing_required_property(self) -> None:
        schema = make_schema()
        f = file_from_content(
            "wiki.yml",
            {"kind": "project", "description": "x"},  # missing Has project status
        )
        issues = validate_file(f, schema)
        assert has_errors(issues)
        assert any("Has project status" in i.message for i in issues)

    def test_missing_kind(self) -> None:
        schema = make_schema()
        f = file_from_content("wiki.yml", {"description": "x"})
        issues = validate_file(f, schema)
        assert has_errors(issues)
        assert any("missing required field: kind" in i.message for i in issues)

    def test_unknown_kind(self) -> None:
        schema = make_schema()
        f = file_from_content("wiki.yml", {"kind": "spaceship"})
        issues = validate_file(f, schema)
        assert has_errors(issues)
        assert any("not installed on the destination wiki" in i.message for i in issues)

    def test_kind_not_in_expected_set(self) -> None:
        schema = make_schema()
        f = file_from_content("wiki.yml", {"kind": "release"})
        issues = validate_file(f, schema, expected_kinds=["project", "hardware_component"])
        assert has_errors(issues)
        assert any("unknown kind" in i.message for i in issues)

    def test_unknown_key_emits_warning(self) -> None:
        schema = make_schema()
        f = file_from_content(
            "housing/wiki.yml",
            {
                "kind": "hardware_component",
                "name": "Housing",
                "project": "MiniXL",
                "made_up_field": "value",
            },
        )
        issues = validate_file(f, schema)
        warnings = [i for i in issues if i.severity == Severity.WARNING]
        assert any("made_up_field" in w.message for w in warnings)
        assert not has_errors(issues)

    def test_structural_keys_dont_warn(self) -> None:
        schema = make_schema()
        f = file_from_content(
            "wiki.yml",
            {
                "kind": "project",
                "description": "x",
                "project_status": "active",
                "specs": [],
                "citation": {},
                "wiki": {"base_path": "MiniXL"},
                "features": [],
            },
        )
        issues = validate_file(f, schema)
        assert issues == []

    def test_doi_case_insensitive(self) -> None:
        """User can write `DOI` or `doi` — both should match Has DOI."""
        schema = make_schema()
        for key in ("DOI", "doi"):
            f = file_from_content(
                "wiki.yml",
                {"kind": "project", "description": "x", "project_status": "active", key: "10.x"},
            )
            issues = validate_file(f, schema)
            warnings = [i for i in issues if i.severity == Severity.WARNING]
            assert warnings == [], f"{key!r} should not warn"


class TestValidateFiles:
    def test_aggregates(self) -> None:
        schema = make_schema()
        files = [
            # both files missing required properties
            file_from_content("wiki.yml", {"kind": "project"}),
            file_from_content("housing/wiki.yml", {"kind": "hardware_component"}),
        ]
        issues = validate_files(files, schema)
        assert len(issues) >= 2
        assert has_errors(issues)


class TestValidationIssueRepr:
    def test_str_format(self) -> None:
        from wiki_repo_bridge.validator import ValidationIssue

        issue = ValidationIssue(Severity.ERROR, "wiki.yml", "x")
        assert str(issue) == "[error] wiki.yml: x"


def test_pytest_collected() -> None:
    # Sanity check: this module loaded without import errors.
    assert True
