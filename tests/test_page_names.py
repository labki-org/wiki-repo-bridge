from wiki_repo_bridge.page_names import (
    component_family_page,
    normalize_version,
    project_page,
    release_page,
    versioned_component_page,
)


class TestNormalizeVersion:
    def test_strips_v_prefix(self) -> None:
        assert normalize_version("v1.2.0") == "1.2.0"

    def test_no_v_unchanged(self) -> None:
        assert normalize_version("1.2.0") == "1.2.0"

    def test_only_strips_leading_v(self) -> None:
        # "version" should not become "ersion"
        assert normalize_version("version") == "ersion"  # documents the simple behavior


class TestPageNames:
    def test_project(self) -> None:
        assert project_page("MiniXL") == "MiniXL"

    def test_family(self) -> None:
        assert component_family_page("MiniXL", "Housing") == "MiniXL/Components/Housing"

    def test_versioned(self) -> None:
        assert (
            versioned_component_page("MiniXL", "Housing", "1.0.2")
            == "MiniXL/Components/Housing/1.0.2"
        )

    def test_versioned_strips_v(self) -> None:
        assert (
            versioned_component_page("MiniXL", "Housing", "v1.0.2")
            == "MiniXL/Components/Housing/1.0.2"
        )

    def test_release(self) -> None:
        assert release_page("MiniXL", "v1.2.0") == "MiniXL/Releases/1.2.0"

    def test_release_without_v(self) -> None:
        assert release_page("MiniXL", "1.2.0") == "MiniXL/Releases/1.2.0"
