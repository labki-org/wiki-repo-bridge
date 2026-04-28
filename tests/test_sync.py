from pathlib import Path

import pytest

from tests.conftest import FakeSite, write_text
from wiki_repo_bridge.schema import (
    CategoryDef,
    PropertyDef,
    PropertyField,
    Schema,
)
from wiki_repo_bridge.sync import (
    SyncError,
    categories_used_by_repo,
    execute_sync,
    plan_sync,
)
from wiki_repo_bridge.validator import has_errors
from wiki_repo_bridge.wiki_client import WikiClient, WriteAction


def make_schema() -> Schema:
    schema = Schema()
    schema.categories["Project"] = CategoryDef(
        name="Project",
        property_fields=[
            PropertyField(name="Has description", required=True),
            PropertyField(name="Has project status", required=True),
            PropertyField(name="Has repository url", required=False),
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
            PropertyField(name="Has source path", required=False),
            PropertyField(name="Has design file url", required=False),
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
            PropertyField(name="Has component", required=False),
            PropertyField(name="Has artifact url", required=False),
            PropertyField(name="Has changelog", required=False),
        ],
    )
    for prop in [
        "Has description", "Has project status", "Has repository url", "Has name",
        "Has project", "Has version", "Has family", "Has latest version",
        "Has source path", "Has design file url", "Has release date", "Has tag",
        "Has component", "Has artifact url", "Has changelog",
    ]:
        schema.properties[prop] = PropertyDef(name=prop, type="Text")
    return schema


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_text(
        tmp_path / "wiki.yml",
        "kind: project\n"
        "name: TestScope\n"
        "description: A test scope\n"
        "project_status: active\n"
        "repository_url: https://github.com/example/testscope\n",
    )
    write_text(
        tmp_path / "housing" / "wiki.yml",
        "kind: hardware_component\n"
        "name: TestScope Housing\n"
        "version: 1.0.2\n"
        "description: 3D printed body\n"
        "source_path: housing\n",
    )
    write_text(
        tmp_path / "optics" / "wiki.yml",
        "kind: hardware_component\n"
        "name: TestScope Optics\n"
        "version: 1.0.0\n"
        "description: Achromatic optics\n"
        "source_path: optics\n",
    )
    return tmp_path


class TestPlanSync:
    def test_clean_plan_has_expected_pages(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        assert not has_errors(plan.issues)
        names = [p.page_name for p in plan.pages]
        assert "TestScope" in names  # project bootstrap
        assert "TestScope/Components/TestScope Housing" in names
        assert "TestScope/Components/TestScope Housing/1.0.2" in names
        assert "TestScope/Components/TestScope Optics" in names
        assert "TestScope/Components/TestScope Optics/1.0.0" in names
        assert "TestScope/Releases/1.2.0" in names

    def test_release_lists_all_versioned_components(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        release = next(p for p in plan.pages if p.page_name.endswith("/Releases/1.2.0"))
        assert "TestScope/Components/TestScope Housing/1.0.2" in release.wikitext
        assert "TestScope/Components/TestScope Optics/1.0.0" in release.wikitext

    def test_immutability_flags(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        for p in plan.pages:
            if p.page_name == "TestScope":
                assert p.bootstrap_only
            elif "/Releases/" in p.page_name or p.page_name.count("/") >= 3:
                # versioned component (3+ slashes) and Release pages are immutable
                assert p.immutable, f"{p.page_name} should be immutable"
            else:
                assert not p.immutable and not p.bootstrap_only

    def test_major_version_mismatch_blocks(self, repo: Path) -> None:
        # tag v2.0.0 but components are at major 1
        plan = plan_sync(repo, "https://wiki.test/api.php", "v2.0.0", schema=make_schema())
        assert has_errors(plan.issues)
        assert any("major does not match" in i.message for i in plan.issues)

    def test_non_semver_tag_skips_major_lint(self, repo: Path) -> None:
        """A manual workflow_dispatch with no tag input passes the branch ref
        (e.g. 'main') as the tag — major-version-match should skip rather than fail."""
        plan = plan_sync(repo, "https://wiki.test/api.php", "main", schema=make_schema())
        assert not any("major does not match" in i.message for i in plan.issues)

    def test_validation_failure_yields_no_pages(self, tmp_path: Path) -> None:
        # Project file missing required Has description
        write_text(tmp_path / "wiki.yml", "kind: project\nname: BadProject\n")
        plan = plan_sync(tmp_path, "https://wiki.test/api.php", "v1.0.0", schema=make_schema())
        assert has_errors(plan.issues)
        assert plan.pages == []


class TestCategoriesUsedByRepo:
    def test_minixl_like(self, repo: Path) -> None:
        cats = categories_used_by_repo(repo)
        assert "Project" in cats
        assert "Hardware component" in cats
        assert "Release" in cats

    def test_missing_repo_returns_defaults(self, tmp_path: Path) -> None:
        cats = categories_used_by_repo(tmp_path / "does-not-exist")
        assert cats == ["Project", "Release"]


class TestExecuteSync:
    def test_writes_all_pages(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        client = WikiClient(site=FakeSite())
        results = execute_sync(plan, client)
        # All pages start absent → all get CREATED
        assert all(r.action == WriteAction.CREATED for r in results)
        assert len(results) == len(plan.pages)

    def test_second_run_skips_immutable_and_bootstrap(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        client = WikiClient(site=FakeSite())
        first = execute_sync(plan, client)
        assert all(r.action == WriteAction.CREATED for r in first)
        # Re-running with the same plan: bootstrap-only and immutable pages should now skip;
        # only the family pages should be UPDATED.
        second = execute_sync(plan, client)
        actions = {r.page_name: r.action for r in second}
        assert actions["TestScope"] == WriteAction.SKIPPED  # project bootstrap
        assert actions["TestScope/Releases/1.2.0"] == WriteAction.SKIPPED  # immutable release
        assert actions["TestScope/Components/TestScope Housing/1.0.2"] == WriteAction.SKIPPED
        assert actions["TestScope/Components/TestScope Housing"] == WriteAction.UPDATED  # family

    def test_dry_run_does_not_edit(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        site = FakeSite()
        client = WikiClient(site=site)
        execute_sync(plan, client, dry_run=True)
        # No actual edits made — every page is still nonexistent
        for page_name in [p.page_name for p in plan.pages]:
            assert site.pages[page_name].edits == []

    def test_refuses_to_execute_with_errors(self, tmp_path: Path) -> None:
        write_text(tmp_path / "wiki.yml", "kind: project\nname: bad\n")  # missing required
        plan = plan_sync(tmp_path, "https://wiki.test/api.php", "v1.0.0", schema=make_schema())
        client = WikiClient(site=FakeSite())
        with pytest.raises(SyncError):
            execute_sync(plan, client)
