from tests.conftest import make_wiki_yml_file
from wiki_repo_bridge.pages import (
    render_component_family,
    render_project_bootstrap,
    render_release,
    render_versioned_component,
)
from wiki_repo_bridge.schema import (
    CategoryDef,
    PropertyDef,
    PropertyField,
    Schema,
)


def make_schema() -> Schema:
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
            PropertyField(name="Has family", required=False),
            PropertyField(name="Has latest version", required=False),
            PropertyField(name="Has description", required=False),
            PropertyField(name="Has hardware type", required=False),
            PropertyField(name="Has source path", required=False),
            PropertyField(name="Has design file url", required=False),
            PropertyField(name="Has release", required=False),
        ],
    )
    schema.categories["Release"] = CategoryDef(
        name="Release",
        property_fields=[
            PropertyField(name="Has name", required=True),
            PropertyField(name="Has version", required=True),
            PropertyField(name="Has release date", required=True),
            PropertyField(name="Has project", required=True),
            PropertyField(name="Has tag", required=False),
            PropertyField(name="Has changelog", required=False),
            PropertyField(name="Has component", required=False),
            PropertyField(name="Has artifact url", required=False),
        ],
    )
    for prop in [
        "Has description", "Has project status", "Has repository url", "Has license",
        "Has DOI", "Has predecessor", "Has name", "Has project", "Has version",
        "Has family", "Has latest version", "Has hardware type", "Has source path",
        "Has design file url", "Has release", "Has release date", "Has tag",
        "Has changelog", "Has component", "Has artifact url",
    ]:
        schema.properties[prop] = PropertyDef(name=prop, type="Text")
    return schema


file_from = make_wiki_yml_file


class TestProjectBootstrap:
    def test_renders_main_template_and_features(self) -> None:
        f = file_from(
            "wiki.yml",
            {
                "kind": "project",
                "name": "MiniXL",
                "description": "Big-FOV miniscope",
                "project_status": "active",
                "repository_url": "https://github.com/miniscope/MiniXL",
                "license": "GPL-3.0",
                "predecessor": "MiniLFOV",
                "features": ["Modular optics", "No soldering"],
            },
        )
        page = render_project_bootstrap(f, make_schema())
        assert page.page_name == "MiniXL"
        assert page.bootstrap_only is True
        assert "{{Project" in page.wikitext
        assert "|has_description=Big-FOV miniscope" in page.wikitext
        assert "|has_repository_url=https://github.com/miniscope/MiniXL" in page.wikitext
        assert "|has_predecessor=MiniLFOV" in page.wikitext
        # Free-form section rendered
        assert "== Features ==" in page.wikitext
        assert "* Modular optics" in page.wikitext

    def test_drops_structural_keys_from_main_template(self) -> None:
        f = file_from(
            "wiki.yml",
            {
                "kind": "project",
                "name": "MiniXL",
                "description": "x",
                "project_status": "active",
                "wiki": {"base_path": "MiniXL"},
                "specs": [],
                "citation": {},
            },
        )
        page = render_project_bootstrap(f, make_schema())
        assert "base_path" not in page.wikitext
        assert "citation" not in page.wikitext


class TestComponentFamily:
    def test_includes_latest_version_pointer(self) -> None:
        f = file_from(
            "housing/wiki.yml",
            {
                "kind": "hardware_component",
                "name": "Housing",
                "description": "3D printed body",
                "hardware_type": "3D_printed",
                "source_path": "housing",
            },
        )
        page = render_component_family(f, "MiniXL", "1.0.2", make_schema())
        assert page.page_name == "MiniXL/Components/Housing"
        assert page.immutable is False
        assert "{{Hardware component" in page.wikitext
        assert "|has_latest_version=MiniXL/Components/Housing/1.0.2" in page.wikitext
        assert "|has_project=MiniXL" in page.wikitext
        assert "|has_description=3D printed body" in page.wikitext
        # Family page must NOT carry version/family — those are versioned-page concerns
        assert "|has_version=" not in page.wikitext
        assert "|has_family=" not in page.wikitext


class TestVersionedComponent:
    def test_immutable_with_full_metadata(self) -> None:
        f = file_from(
            "housing/wiki.yml",
            {
                "kind": "hardware_component",
                "name": "Housing",
                "version": "1.0.2",
                "description": "3D printed body",
                "hardware_type": "3D_printed",
                "source_path": "housing",
            },
        )
        page = render_versioned_component(
            f,
            project_name="MiniXL",
            version="1.0.2",
            tag="v1.2.0",
            repository_url="https://github.com/miniscope/MiniXL",
            schema=make_schema(),
        )
        assert page.page_name == "MiniXL/Components/Housing/1.0.2"
        assert page.immutable is True
        assert "|has_version=1.0.2" in page.wikitext
        assert "|has_family=MiniXL/Components/Housing" in page.wikitext
        assert "|has_project=MiniXL" in page.wikitext
        # Tag-pinned design-file URL is computed from repo+source_path+tag
        assert (
            "|has_design_file_url=https://github.com/miniscope/MiniXL/tree/v1.2.0/housing"
            in page.wikitext
        )


class TestRelease:
    def test_release_page(self) -> None:
        project = file_from(
            "wiki.yml",
            {
                "kind": "project",
                "name": "MiniXL",
                "description": "x",
                "project_status": "active",
            },
        )
        page = render_release(
            project,
            tag="v1.2.0",
            component_pages=[
                "MiniXL/Components/Housing/1.0.2",
                "MiniXL/Components/Optics/1.0.0",
            ],
            release_date="2025-04-14",
            changelog="Updated firmware",
            artifact_url="https://github.com/miniscope/MiniXL/tree/v1.2.0",
            schema=make_schema(),
        )
        assert page.page_name == "MiniXL/Releases/1.2.0"
        assert page.immutable is True
        assert "{{Release" in page.wikitext
        assert "|has_tag=v1.2.0" in page.wikitext
        assert "|has_version=1.2.0" in page.wikitext
        assert "|has_release_date=2025-04-14" in page.wikitext
        assert "|has_name=MiniXL Release 1.2.0" in page.wikitext
        assert (
            "|has_component=MiniXL/Components/Housing/1.0.2, MiniXL/Components/Optics/1.0.0"
            in page.wikitext
        )
        assert "|has_changelog=Updated firmware" in page.wikitext
