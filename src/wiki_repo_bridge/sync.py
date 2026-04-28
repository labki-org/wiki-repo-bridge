"""End-to-end sync orchestration: walk a repo, validate every wiki.yml against
the destination wiki's installed schema, render the page tree for the given tag,
and write each page honoring bootstrap-only / immutable rules.

Designed to be the single entry point both the CLI and the GitHub Action call.
The function is split into ``plan_sync`` (pure: figures out what would be written)
and ``execute_sync`` (impure: performs the writes) so callers can dry-run safely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from wiki_repo_bridge import page_names
from wiki_repo_bridge.images import (
    ImageUpload,
    alias_filename,
    discover_images,
    wiki_filename,
)
from wiki_repo_bridge.pages import (
    PageContent,
    render_component,
    render_component_redirect,
    render_project,
    render_release,
)
from wiki_repo_bridge.readme import ReadmeContent, convert_readme, discover_readme
from wiki_repo_bridge.schema import Schema
from wiki_repo_bridge.validator import (
    Kind,
    Severity,
    ValidationIssue,
    has_errors,
    kind_to_category_name,
    validate_files,
)
from wiki_repo_bridge.walker import (
    WikiYmlError,
    WikiYmlFile,
    find_component_files,
    find_project_file,
    find_wiki_yml_files,
)
from wiki_repo_bridge.wiki_client import WikiClient, WriteResult
from wiki_repo_bridge.wikitext import semver_tuple

log = logging.getLogger(__name__)
_pypandoc_missing_warned = False

_SUPPORTED_KINDS = [k.value for k in Kind]


class SyncError(Exception):
    """Raised when sync planning fails — typically because validation produced errors."""


@dataclass
class SyncPlan:
    """The full set of pages and image uploads a sync run would push to one wiki."""

    wiki_url: str
    project_name: str
    tag: str
    pages: list[PageContent] = field(default_factory=list)
    image_uploads: list[ImageUpload] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


def plan_sync(
    repo_path: Path | str,
    wiki_url: str,
    tag: str,
    *,
    schema: Schema,
    release_date: str | None = None,
    changelog: str | None = None,
    files: list[WikiYmlFile] | None = None,
) -> SyncPlan:
    """Walk the repo and produce a :class:`SyncPlan` for one destination wiki.

    ``schema`` is the installed schema fetched from the destination wiki — kept
    as a parameter so callers control fetching/caching strategy. Pre-walked
    ``files`` may be passed in to avoid re-walking when planning across multiple
    wikis. Lints the component-major-version-match rule before building pages.
    """
    if files is None:
        files = find_wiki_yml_files(repo_path)
    project_file = find_project_file(files)
    component_files = find_component_files(files)

    issues = validate_files(files, schema, expected_kinds=_SUPPORTED_KINDS)
    issues.extend(_check_major_version_match(tag, component_files))
    project_version = page_names.normalize_version(tag)

    project_name = project_file.content["name"]
    repo_root = Path(repo_path)

    project_images, project_image_issues = _resolve_images(
        project_file, project_name, component=None,
        version=project_version, repo_root=repo_root,
    )
    issues.extend(project_image_issues)

    component_image_lists: list[list[ImageUpload]] = []
    for cf in component_files:
        component_version = str(cf.content.get("version", "0.0.0"))
        component_name = cf.content["name"]
        ci, ci_issues = _resolve_images(
            cf, project_name, component=component_name,
            version=component_version, repo_root=repo_root,
        )
        component_image_lists.append(ci)
        issues.extend(ci_issues)

    plan = SyncPlan(
        wiki_url=wiki_url,
        project_name=project_name,
        tag=tag,
        issues=issues,
    )
    if has_errors(issues):
        return plan

    repository_url = project_file.content.get("repository_url")
    rdate = release_date or date.today().isoformat()

    project_readme = _maybe_load_readme(
        project_file, project_images, repository_url, tag, repo_root,
    )

    plan.pages.append(
        render_project(project_file, schema, images=project_images)
    )
    plan.image_uploads.extend(project_images)

    component_versioned_pages: list[str] = []
    for cf, ci in zip(component_files, component_image_lists, strict=True):
        version = str(cf.content.get("version", "0.0.0"))
        component_name = cf.content["name"]
        component_readme = _maybe_load_readme(cf, ci, repository_url, tag, repo_root)

        plan.pages.append(
            render_component(
                cf,
                project_name=project_name,
                version=version,
                tag=tag,
                repository_url=repository_url,
                schema=schema,
                images=ci,
                readme=component_readme,
            )
        )
        plan.pages.append(
            render_component_redirect(project_name, component_name, version)
        )
        plan.image_uploads.extend(ci)
        component_versioned_pages.append(
            page_names.component_versioned_page(project_name, component_name, version)
        )

    artifact_url = (
        page_names.repo_tree_url(repository_url, tag) if repository_url else None
    )
    plan.pages.append(
        render_release(
            project_file,
            tag=tag,
            component_pages=component_versioned_pages,
            release_date=rdate,
            changelog=changelog,
            artifact_url=artifact_url,
            schema=schema,
            images=project_images,
            readme=project_readme,
        )
    )
    return plan


def _maybe_load_readme(
    file: WikiYmlFile,
    images: list[ImageUpload],
    repository_url: str | None,
    tag: str,
    repo_root: Path,
) -> ReadmeContent | None:
    """Find and convert ``README.md`` next to ``file``, honoring the ``readme: false`` opt-out.

    Pandoc isn't a hard dep — if the README directive is enabled but ``pypandoc`` isn't
    installed, we log and skip rather than failing the whole sync.
    """
    if file.content.get("readme") is False:
        return None
    md_path = discover_readme(file.path.parent)
    if md_path is None:
        return None
    try:
        return convert_readme(
            md_path, images=images, repository_url=repository_url,
            tag=tag, repo_root=repo_root,
        )
    except ImportError:
        global _pypandoc_missing_warned
        if not _pypandoc_missing_warned:
            log.warning(
                "pypandoc not installed — skipping README sync entirely. "
                "Install with `pip install pypandoc-binary` to embed READMEs on wiki pages."
            )
            _pypandoc_missing_warned = True
        return None


def _resolve_images(
    file: WikiYmlFile,
    project_name: str,
    *,
    component: str | None,
    version: str,
    repo_root: Path,
) -> tuple[list[ImageUpload], list[ValidationIssue]]:
    """Discover and name the images declared on ``file``. Returns (uploads, issues)."""
    decls, errors = discover_images(file, repo_root=repo_root)
    issues = [
        ValidationIssue(severity=Severity.ERROR, file=str(file.relative_path), message=msg)
        for msg in errors
    ]
    uploads: list[ImageUpload] = []
    for d in decls:
        uploads.append(
            ImageUpload(
                abs_path=d.abs_path,
                versioned_name=wiki_filename(
                    project=project_name, component=component, version=version,
                    stem=d.stem, suffix=d.suffix,
                ),
                alias_name=alias_filename(
                    project=project_name, component=component,
                    stem=d.stem, suffix=d.suffix,
                ),
                caption=d.caption,
                kind=d.kind,
            )
        )
    return uploads, issues


def execute_sync(
    plan: SyncPlan, client: WikiClient, *, edit_summary: str | None = None,
    dry_run: bool = False,
) -> list[WriteResult]:
    """Push the plan to the wiki: upload images, then write pages.

    Images are uploaded first under both their versioned and unversioned alias names,
    so any thumbnail references on Component / Project / Release pages resolve when
    those pages are written.

    Returns one ``WriteResult`` per page; image-upload outcomes are not surfaced here
    (they are best-effort and cannot block the page sync).
    """
    if has_errors(plan.issues):
        raise SyncError(
            f"Refusing to execute sync to {plan.wiki_url}: validation failed with errors"
        )
    summary = edit_summary or f"wiki-repo-bridge sync ({plan.tag})"
    mode = "DRY-RUN" if dry_run else "LIVE"
    log.info("[%s] Executing sync to %s — %d images, %d pages",
             mode, plan.wiki_url, len(plan.image_uploads), len(plan.pages))

    if plan.image_uploads:
        log.info("Uploading %d images (each as versioned + alias)", len(plan.image_uploads))
    for upload in plan.image_uploads:
        client.upload_file(upload.abs_path, upload.versioned_name,
                           description=f"versioned image for {plan.tag}", dry_run=dry_run)
        client.upload_file(upload.abs_path, upload.alias_name,
                           description="latest alias", dry_run=dry_run)

    log.info("Writing %d pages", len(plan.pages))
    return [client.write_page(p, edit_summary=summary, dry_run=dry_run) for p in plan.pages]


def categories_used_by_repo(
    repo_path: Path | str, files: list[WikiYmlFile] | None = None
) -> list[str]:
    """Categories the repo writes pages for. Always includes Project and Release
    since the bridge writes them even when no wiki.yml declares them.

    Pass pre-walked ``files`` to avoid re-walking the repo.
    """
    if files is None:
        try:
            files = find_wiki_yml_files(repo_path)
        except WikiYmlError:
            return ["Project", "Release"]

    cats = {"Project", "Release"}
    for f in files:
        if kind := f.kind:
            cats.add(kind_to_category_name(kind))
    return sorted(cats)


def _check_major_version_match(
    tag: str, component_files: list[WikiYmlFile]
) -> list[ValidationIssue]:
    """Lint: tag must be semver-formatted (``v1.2.0`` or ``1.2.0``) and every
    component's major version must match the project tag's major version."""
    try:
        project_major, *_ = semver_tuple(tag)
    except ValueError:
        return [ValidationIssue(
            severity=Severity.ERROR,
            file="<tag>",
            message=(
                f"tag {tag!r} must be semver-formatted (e.g., v1.2.0). "
                "For testing without a real release, use --tag v0.0.0."
            ),
        )]

    issues: list[ValidationIssue] = []
    for cf in component_files:
        component_version = str(cf.content.get("version", "")).strip()
        if not component_version:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                file=str(cf.relative_path),
                message="missing required field: version (e.g., '0.1.0')",
            ))
            continue
        try:
            component_major, *_ = semver_tuple(component_version)
        except ValueError:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                file=str(cf.relative_path),
                message=f"component version {component_version!r} is not semver-formatted",
            ))
            continue
        if component_major != project_major:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    file=str(cf.relative_path),
                    message=(
                        f"component version {component_version!r} major ({component_major}) "
                        f"does not match project tag {tag!r} major ({project_major})"
                    ),
                )
            )
    return issues
