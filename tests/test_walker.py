from pathlib import Path

import pytest

from tests.conftest import write_text
from wiki_repo_bridge.walker import (
    WikiYmlError,
    find_component_files,
    find_project_file,
    find_wiki_yml_files,
)


@pytest.fixture
def minixl_like_repo(tmp_path: Path) -> Path:
    """A miniature MiniXL-shaped repo with one project + four hardware components."""
    write_text(
        tmp_path / "wiki.yml",
        "kind: project\nname: TestScope\nversion: 1.0.0\nrepository_url: https://example.org/repo\n",
    )
    write_text(
        tmp_path / "housing" / "wiki.yml",
        "kind: hardware_component\nname: TestScope Housing\nversion: 1.0.0\n",
    )
    write_text(
        tmp_path / "optics" / "wiki.yml",
        "kind: hardware_component\nname: TestScope Optics\nversion: 1.0.0\n",
    )
    write_text(
        tmp_path / "pcb" / "main" / "wiki.yml",
        "kind: hardware_component\nname: TestScope PCB\nversion: 1.0.0\n",
    )
    write_text(
        tmp_path / "baseplate" / "wiki.yml",
        "kind: hardware_component\nname: TestScope Baseplate\nversion: 1.0.0\n",
    )
    # Decoy files that should be ignored
    write_text(tmp_path / "README.md", "# scope")
    write_text(tmp_path / ".github" / "workflows" / "ci.yml", "name: CI\n")
    write_text(tmp_path / ".venv" / "wiki.yml", "kind: project\n")  # inside skip dir
    return tmp_path


class TestFindWikiYmlFiles:
    def test_finds_all_real_wiki_yml(self, minixl_like_repo: Path) -> None:
        files = find_wiki_yml_files(minixl_like_repo)
        rels = [str(f.relative_path) for f in files]
        assert rels == [
            "wiki.yml",
            "baseplate/wiki.yml",
            "housing/wiki.yml",
            "optics/wiki.yml",
            "pcb/main/wiki.yml",
        ]

    def test_skips_hidden_and_build_dirs(self, minixl_like_repo: Path) -> None:
        files = find_wiki_yml_files(minixl_like_repo)
        for f in files:
            assert ".venv" not in f.relative_path.parts
            assert ".github" not in f.relative_path.parts

    def test_kind_extraction(self, minixl_like_repo: Path) -> None:
        files = find_wiki_yml_files(minixl_like_repo)
        kinds = sorted({f.kind for f in files})
        assert kinds == ["hardware_component", "project"]

    def test_missing_repo_raises(self, tmp_path: Path) -> None:
        with pytest.raises(WikiYmlError, match="not a directory"):
            find_wiki_yml_files(tmp_path / "does-not-exist")

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        write_text(tmp_path / "wiki.yml", "kind: project\n  bad-indent: : :")
        with pytest.raises(WikiYmlError, match="Could not parse YAML"):
            find_wiki_yml_files(tmp_path)

    def test_top_level_must_be_mapping(self, tmp_path: Path) -> None:
        write_text(tmp_path / "wiki.yml", "- just\n- a\n- list\n")
        with pytest.raises(WikiYmlError, match="must be a YAML mapping"):
            find_wiki_yml_files(tmp_path)


class TestFindProjectAndComponents:
    def test_find_project(self, minixl_like_repo: Path) -> None:
        files = find_wiki_yml_files(minixl_like_repo)
        project = find_project_file(files)
        assert project.kind == "project"
        assert project.content["name"] == "TestScope"

    def test_find_components(self, minixl_like_repo: Path) -> None:
        files = find_wiki_yml_files(minixl_like_repo)
        components = find_component_files(files)
        names = sorted(f.content["name"] for f in components)
        assert names == [
            "TestScope Baseplate",
            "TestScope Housing",
            "TestScope Optics",
            "TestScope PCB",
        ]

    def test_no_project_raises(self, tmp_path: Path) -> None:
        write_text(tmp_path / "housing" / "wiki.yml", "kind: hardware_component\nname: x\n")
        files = find_wiki_yml_files(tmp_path)
        with pytest.raises(WikiYmlError, match=r"No wiki\.yml with kind: project"):
            find_project_file(files)

    def test_multiple_projects_raises(self, tmp_path: Path) -> None:
        write_text(tmp_path / "wiki.yml", "kind: project\nname: A\n")
        write_text(tmp_path / "nested" / "wiki.yml", "kind: project\nname: B\n")
        files = find_wiki_yml_files(tmp_path)
        with pytest.raises(WikiYmlError, match="Multiple kind: project"):
            find_project_file(files)
