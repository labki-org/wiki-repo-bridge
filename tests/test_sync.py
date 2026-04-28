from pathlib import Path

import pytest

from tests.conftest import FakeSite, make_schema, write_text
from wiki_repo_bridge.sync import (
    SyncError,
    categories_used_by_repo,
    execute_sync,
    plan_sync,
)
from wiki_repo_bridge.validator import has_errors
from wiki_repo_bridge.wiki_client import WikiClient, WriteAction


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
        assert "TestScope" in names
        assert "TestScope/Component/TestScope Housing" in names
        assert "TestScope/Component/TestScope Optics" in names
        assert "TestScope/Release/1.2.0" in names
        # Archive subpages are not in the plan — they're created by page-move at execute time.
        assert "TestScope/Component/TestScope Housing/v1.0.2" not in names
        assert "TestScope/Component/TestScope Housing/1.0.2" not in names

    def test_release_links_per_version_archive_pages(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        release = next(p for p in plan.pages if p.page_name.endswith("/Release/1.2.0"))
        assert "TestScope/Component/TestScope Housing/v1.0.2" in release.wikitext
        assert "TestScope/Component/TestScope Optics/v1.0.0" in release.wikitext

    def test_write_modes(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        for p in plan.pages:
            if "/Release/" in p.page_name:
                assert p.immutable, f"{p.page_name} should be immutable"
                assert p.managed_body is None
            else:
                # Project + Component pages are managed-section.
                assert p.managed_body is not None, f"{p.page_name} should be managed"
                assert not p.immutable
                assert not p.bootstrap_only

    def test_major_version_mismatch_blocks(self, repo: Path) -> None:
        # tag v2.0.0 but components are at major 1
        plan = plan_sync(repo, "https://wiki.test/api.php", "v2.0.0", schema=make_schema())
        assert has_errors(plan.issues)
        assert any("does not match" in i.message for i in plan.issues)

    def test_non_semver_tag_errors(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "main", schema=make_schema())
        assert has_errors(plan.issues)
        assert any("must be semver-formatted" in i.message for i in plan.issues)

    def test_missing_component_version_errors(self, tmp_path: Path) -> None:
        write_text(tmp_path / "wiki.yml",
                   "kind: project\nname: P\ndescription: x\nproject_status: active\n")
        write_text(tmp_path / "h" / "wiki.yml",
                   "kind: hardware_component\nname: H\n")  # version: omitted
        plan = plan_sync(tmp_path, "https://wiki.test/api.php", "v1.0.0", schema=make_schema())
        assert has_errors(plan.issues)
        assert any("missing required field: version" in i.message for i in plan.issues)
        assert plan.pages == []

    def test_non_semver_component_version_errors(self, tmp_path: Path) -> None:
        write_text(tmp_path / "wiki.yml",
                   "kind: project\nname: P\ndescription: x\nproject_status: active\n")
        write_text(tmp_path / "h" / "wiki.yml",
                   "kind: hardware_component\nname: H\nversion: not-a-version\n")
        plan = plan_sync(tmp_path, "https://wiki.test/api.php", "v1.0.0", schema=make_schema())
        assert has_errors(plan.issues)
        assert any("not semver-formatted" in i.message for i in plan.issues)

    def test_validation_failure_yields_no_pages(self, tmp_path: Path) -> None:
        # Project file missing required Has description
        write_text(tmp_path / "wiki.yml", "kind: project\nname: BadProject\n")
        plan = plan_sync(tmp_path, "https://wiki.test/api.php", "v1.0.0", schema=make_schema())
        assert has_errors(plan.issues)
        assert plan.pages == []


class TestImagesInPlan:
    """Image discovery integrates with plan_sync — uploads land in plan.image_uploads
    and references appear on the right pages."""

    @pytest.fixture
    def repo_with_images(self, tmp_path: Path) -> Path:
        write_text(
            tmp_path / "wiki.yml",
            "kind: project\nname: TestScope\ndescription: x\nproject_status: active\n"
            "repository_url: https://github.com/example/testscope\n"
            "images:\n  - {path: assets/hero.png, caption: Project hero}\n",
        )
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "hero.png").write_bytes(b"x")
        write_text(
            tmp_path / "housing" / "wiki.yml",
            "kind: hardware_component\nname: Housing\nversion: 1.0.0\n"
            "description: 3D printed body\n"
            "images:\n  - {path: render.png, caption: Render of housing}\n",
        )
        (tmp_path / "housing" / "render.png").write_bytes(b"y")
        return tmp_path

    def test_image_uploads_collected(self, repo_with_images: Path) -> None:
        plan = plan_sync(
            repo_with_images, "https://wiki.test/api.php", "v1.0.0", schema=make_schema(),
        )
        assert not has_errors(plan.issues)
        names = {u.versioned_name for u in plan.image_uploads}
        assert "TestScope_v1.0.0_hero.png" in names
        assert "TestScope_Housing_v1.0.0_render.png" in names
        aliases = {u.alias_name for u in plan.image_uploads}
        assert "TestScope_hero.png" in aliases
        assert "TestScope_Housing_render.png" in aliases

    def test_component_page_references_alias(self, repo_with_images: Path) -> None:
        plan = plan_sync(
            repo_with_images, "https://wiki.test/api.php", "v1.0.0", schema=make_schema(),
        )
        housing = next(p for p in plan.pages if p.page_name.endswith("/Housing"))
        assert "File:TestScope_Housing_render.png" in housing.managed_body
        # SMW annotation is present so other pages can query
        assert "[[Has image::File:TestScope_Housing_render.png]]" in housing.managed_body

    def test_release_page_references_only_project_images(self, repo_with_images: Path) -> None:
        plan = plan_sync(
            repo_with_images, "https://wiki.test/api.php", "v1.0.0", schema=make_schema(),
        )
        release = next(p for p in plan.pages if "/Release/" in p.page_name)
        # Project image (versioned name) is referenced; component images live on
        # the Component pages, not duplicated on Release.
        assert "TestScope_v1.0.0_hero.png" in release.wikitext
        assert "TestScope_Housing_v1.0.0_render.png" not in release.wikitext
        assert "has_image=" in release.wikitext

    def test_missing_image_blocks_plan(self, tmp_path: Path) -> None:
        write_text(tmp_path / "wiki.yml",
                   "kind: project\nname: P\ndescription: x\nproject_status: active\n"
                   "images:\n  - {path: nope.png}\n")
        plan = plan_sync(tmp_path, "https://wiki.test/api.php", "v1.0.0", schema=make_schema())
        assert has_errors(plan.issues)
        assert plan.pages == []  # plan abandons rendering when validation fails


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

    def test_second_run_skips_immutable_updates_managed(self, repo: Path) -> None:
        plan = plan_sync(repo, "https://wiki.test/api.php", "v1.2.0", schema=make_schema())
        client = WikiClient(site=FakeSite())
        first = execute_sync(plan, client)
        assert all(r.action == WriteAction.CREATED for r in first)
        # Re-running with the same plan: immutable pages skip; managed pages are
        # UPDATED (the RMW preserves anything outside markers but rewrites the block).
        second = execute_sync(plan, client)
        actions = {r.page_name: r.action for r in second}
        assert actions["TestScope/Release/1.2.0"] == WriteAction.SKIPPED
        assert actions["TestScope"] == WriteAction.UPDATED
        assert actions["TestScope/Component/TestScope Housing"] == WriteAction.UPDATED

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
