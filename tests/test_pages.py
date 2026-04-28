from tests.conftest import make_schema, make_wiki_yml_file
from wiki_repo_bridge.pages import (
    render_component,
    render_project,
    render_release,
)

file_from = make_wiki_yml_file


class TestProject:
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
        page = render_project(f, make_schema())
        assert page.page_name == "MiniXL"
        assert page.bootstrap_only is False
        assert page.immutable is False
        assert page.managed_body is not None
        assert "{{Project" in page.managed_body
        assert "|has_description=Big-FOV miniscope" in page.managed_body
        assert "|has_repository_url=https://github.com/miniscope/MiniXL" in page.managed_body
        assert "|has_predecessor=MiniLFOV" in page.managed_body
        # Free-form section rendered inside the managed block
        assert "== Features ==" in page.managed_body
        assert "* Modular optics" in page.managed_body
        # Scaffold is the human-editable wrapper above the markers
        assert "MiniXL" in page.scaffold

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
        page = render_project(f, make_schema())
        assert "base_path" not in page.managed_body
        assert "citation" not in page.managed_body

    def test_default_project_status_when_missing(self) -> None:
        f = file_from("wiki.yml", {"kind": "project", "name": "MiniXL", "description": "x"})
        page = render_project(f, make_schema())
        assert "|has_project_status=active" in page.managed_body

    def test_explicit_project_status_overrides_default(self) -> None:
        f = file_from(
            "wiki.yml",
            {
                "kind": "project", "name": "MiniXL", "description": "x",
                "project_status": "archived",
            },
        )
        page = render_project(f, make_schema())
        assert "|has_project_status=archived" in page.managed_body
        assert "|has_project_status=active" not in page.managed_body


class TestDesignFilesRendering:
    def test_dict_with_list_values_renders_as_nested_bullets(self) -> None:
        f = file_from(
            "housing/wiki.yml",
            {
                "kind": "hardware_component",
                "name": "Housing",
                "version": "1.0.0",
                "description": "x",
                "design_files": {
                    "fusion_360": ["a.f3d", "b.f3d"],
                    "fabrication": "single.stl",
                },
            },
        )
        page = render_component(
            f, project_name="P", version="1.0.0", tag="v1.0.0",
            repository_url=None, schema=make_schema(),
        )
        assert "* '''fusion 360''':\n** a.f3d\n** b.f3d" in page.managed_body
        assert "* '''fabrication''': single.stl" in page.managed_body
        # Critical: no Python list literal leaking into wikitext
        assert "['a.f3d'" not in page.managed_body


class TestComponent:
    def test_managed_body_with_full_metadata(self) -> None:
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
        page = render_component(
            f,
            project_name="MiniXL",
            version="1.0.2",
            tag="v1.2.0",
            repository_url="https://github.com/miniscope/MiniXL",
            schema=make_schema(),
        )
        assert page.page_name == "MiniXL/Components/Housing"
        assert page.immutable is False
        assert page.managed_body is not None
        assert "{{Hardware component" in page.managed_body
        assert "|has_version=1.0.2" in page.managed_body
        assert "|has_project=MiniXL" in page.managed_body
        # has_latest_version is dropped — Component page's own has_version IS the latest.
        assert "|has_latest_version=" not in page.managed_body
        # version is recorded on the PageContent for the executor's archive flow.
        assert page.version == "1.0.2"
        assert "|has_description=3D printed body" in page.managed_body
        # Has family is dropped — archive parents are structural (subpage relationship).
        assert "|has_family=" not in page.managed_body
        # Tag-pinned design-file URL still computed from repo+source_path+tag
        assert (
            "|has_design_file_url=https://github.com/miniscope/MiniXL/tree/v1.2.0/housing"
            in page.managed_body
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
                "responsible_party": "Aharoni Lab",
            },
        )
        page = render_release(
            project,
            tag="v1.2.0",
            component_pages=[
                "MiniXL/Components/Housing/v1.0.2",
                "MiniXL/Components/Optics/v1.0.0",
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
            "|has_component=MiniXL/Components/Housing/v1.0.2, MiniXL/Components/Optics/v1.0.0"
            in page.wikitext
        )
        assert "|has_changelog=Updated firmware" in page.wikitext
        assert "|has_responsible_party=Aharoni Lab" in page.wikitext

    def test_release_omits_responsible_party_when_project_lacks_one(self) -> None:
        project = file_from(
            "wiki.yml",
            {"kind": "project", "name": "MiniXL", "description": "x", "project_status": "active"},
        )
        page = render_release(
            project, tag="v1.2.0", component_pages=["MiniXL/Components/X/v1.0.0"],
            release_date="2025-04-14", schema=make_schema(),
        )
        assert "has_responsible_party" not in page.wikitext
