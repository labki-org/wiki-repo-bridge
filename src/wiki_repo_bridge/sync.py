"""End-to-end sync orchestration: walk a repo, validate every wiki.yml against
the destination wiki's installed schema, render the page tree for the given tag,
and write each page honoring bootstrap-only / immutable rules.

Designed to be the single entry point both the CLI and the GitHub Action call.
The function is split into ``plan_sync`` (pure: figures out what would be written)
and ``execute_sync`` (impure: performs the writes) so callers can dry-run safely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from wiki_repo_bridge import page_names
from wiki_repo_bridge.pages import (
    PageContent,
    render_component_family,
    render_project_bootstrap,
    render_release,
    render_versioned_component,
)
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

_SUPPORTED_KINDS = [k.value for k in Kind]


class SyncError(Exception):
    """Raised when sync planning fails — typically because validation produced errors."""


@dataclass
class SyncPlan:
    """The full set of pages a sync run would write to one destination wiki."""

    wiki_url: str
    project_name: str
    tag: str
    pages: list[PageContent] = field(default_factory=list)
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

    # Validate every file against the destination wiki's schema. Stop if errors.
    issues = validate_files(files, schema, expected_kinds=_SUPPORTED_KINDS)
    issues.extend(_check_major_version_match(tag, component_files))

    plan = SyncPlan(
        wiki_url=wiki_url,
        project_name=project_file.content["name"],
        tag=tag,
        issues=issues,
    )
    if has_errors(issues):
        return plan

    project_name = project_file.content["name"]
    repository_url = project_file.content.get("repository_url")
    rdate = release_date or date.today().isoformat()

    # Project bootstrap stub (skipped on write if page exists)
    plan.pages.append(render_project_bootstrap(project_file, schema))

    # Per-component family + versioned snapshot
    component_versioned_pages: list[str] = []
    for cf in component_files:
        version = str(cf.content.get("version", "0.0.0"))
        component_name = cf.content["name"]

        plan.pages.append(
            render_component_family(cf, project_name, version, schema)
        )
        plan.pages.append(
            render_versioned_component(
                cf,
                project_name=project_name,
                version=version,
                tag=tag,
                repository_url=repository_url,
                schema=schema,
            )
        )
        component_versioned_pages.append(
            page_names.versioned_component_page(project_name, component_name, version)
        )

    # Release manifest
    artifact_url = (
        f"{repository_url.rstrip('/')}/tree/{tag}" if repository_url else None
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
        )
    )
    return plan


def execute_sync(
    plan: SyncPlan, client: WikiClient, *, edit_summary: str | None = None,
    dry_run: bool = False,
) -> list[WriteResult]:
    """Write every page in the plan via ``client``. Returns one WriteResult per page."""
    if has_errors(plan.issues):
        raise SyncError(
            f"Refusing to execute sync to {plan.wiki_url}: validation failed with errors"
        )
    summary = edit_summary or f"wiki-repo-bridge sync ({plan.tag})"
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


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.+-]+)?$")


def _check_major_version_match(
    tag: str, component_files: list[WikiYmlFile]
) -> list[ValidationIssue]:
    """Lint: tag must be semver-formatted (``v1.2.0`` or ``1.2.0``) and every
    component's major version must match the project tag's major version."""
    project_version = page_names.normalize_version(tag)
    if not _SEMVER_RE.match(project_version):
        return [ValidationIssue(
            severity=Severity.ERROR,
            file="<tag>",
            message=(
                f"tag {tag!r} must be semver-formatted (e.g., v1.2.0). "
                "For testing without a real release, use --tag v0.0.0."
            ),
        )]
    project_major = project_version.split(".", 1)[0]
    issues: list[ValidationIssue] = []
    for cf in component_files:
        component_version = str(cf.content.get("version", ""))
        if not component_version:
            continue
        if not _SEMVER_RE.match(page_names.normalize_version(component_version)):
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                file=str(cf.relative_path),
                message=f"component version {component_version!r} is not semver-formatted",
            ))
            continue
        component_major = component_version.split(".", 1)[0]
        if component_major != project_major:
            issues.append(
                ValidationIssue(
                    severity=Severity.ERROR,
                    file=str(cf.relative_path),
                    message=(
                        f"component version {component_version!r} major does not match "
                        f"project tag {tag!r} major ({project_major!r})"
                    ),
                )
            )
    return issues
