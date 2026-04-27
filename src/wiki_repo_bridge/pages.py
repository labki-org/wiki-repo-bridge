"""Build the wikitext for each kind of page the bridge writes.

There are four shapes:

* **Project bootstrap** — written once if the page doesn't exist; humans curate after.
* **Component family** — un-versioned canonical page; rewritten each release to update
  ``Has latest version``.
* **Versioned component** — immutable per-version snapshot.
* **Release** — immutable per-tag manifest that bundles versioned components.

Each renderer takes the parsed wiki.yml file plus context (project name, tag, schema)
and returns a :class:`PageContent` describing what to write where.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wiki_repo_bridge import page_names
from wiki_repo_bridge.schema import CategoryDef, Schema
from wiki_repo_bridge.validator import (
    STRUCTURAL_KEYS,
    kind_to_category_name,
    yaml_key_to_property_name,
)
from wiki_repo_bridge.walker import WikiYmlFile
from wiki_repo_bridge.wikitext import (
    render_bullet_list,
    render_section,
    render_template,
)


@dataclass(frozen=True)
class PageContent:
    """One page the bridge intends to write."""

    page_name: str
    wikitext: str
    immutable: bool = False
    """Versioned-Component and Release pages are immutable — bridge will skip if present."""

    bootstrap_only: bool = False
    """Project pages are only written on first run; never overwritten thereafter."""


def _content_kwargs(file: WikiYmlFile, category: CategoryDef) -> dict[str, str]:
    """Pick wiki.yml keys whose names map to known property fields on ``category``,
    convert them to SemanticSchemas template parameter form (``has_xxx_yyy``),
    and return them in the canonical category-field order so output is deterministic.

    Unknown keys (already warned about by the validator) and structural keys are dropped.
    """
    field_order_lower = {f.name.lower(): f.name for f in category.property_fields}

    chosen: dict[str, Any] = {}
    for key, value in file.content.items():
        if key in STRUCTURAL_KEYS:
            continue
        prop_name = yaml_key_to_property_name(key)
        if prop_name.lower() not in field_order_lower:
            continue
        param = "has_" + key.replace("_", "_") if key.startswith("has_") else "has_" + key
        chosen[param] = value

    # Re-key in field declaration order for stable output
    ordered: dict[str, Any] = {}
    for field_name in [f.name for f in category.property_fields]:
        param = "has_" + field_name[len("Has "):].replace(" ", "_").lower()
        if param in chosen:
            ordered[param] = chosen[param]
    # Append anything left (shouldn't happen unless there's a key the schema doesn't list)
    for k, v in chosen.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def _free_text_sections(file: WikiYmlFile) -> str:
    """Render any free-form structural blocks (features, design_files) as wiki sections."""
    sections: list[str] = []
    if features := file.content.get("features"):
        if isinstance(features, list) and features:
            sections.append(render_section("Features", render_bullet_list(features)))
    if design_files := file.content.get("design_files"):
        if isinstance(design_files, dict):
            lines = []
            for label, value in design_files.items():
                lines.append(f"* '''{label.replace('_', ' ')}''': {value}")
            sections.append(render_section("Design Files", "\n".join(lines)))
    return "\n\n".join(sections)


def render_project_bootstrap(file: WikiYmlFile, schema: Schema) -> PageContent:
    """Bootstrap stub for the Project page. Written only if the page does not exist."""
    category = schema.categories["Project"]
    main = render_template("Project", _content_kwargs(file, category))

    body_parts = [main]
    if extras := _free_text_sections(file):
        body_parts.append(extras)

    project_name = file.content["name"]
    return PageContent(
        page_name=page_names.project_page(project_name),
        wikitext="\n\n".join(body_parts) + "\n",
        bootstrap_only=True,
    )


def render_component_family(
    file: WikiYmlFile, project_name: str, latest_version: str, schema: Schema
) -> PageContent:
    """Canonical (un-versioned) component page; rewritten on each release."""
    category_name = kind_to_category_name(file.kind or "")
    category = schema.categories[category_name]
    component_name = file.content["name"]

    kwargs = _content_kwargs(file, category)
    # The Family page intentionally omits has_version and has_family —
    # those belong on the per-version snapshot, not the canonical page.
    kwargs.pop("has_version", None)
    kwargs.pop("has_family", None)
    kwargs["has_project"] = project_name
    kwargs["has_latest_version"] = page_names.versioned_component_page(
        project_name, component_name, latest_version
    )

    body = render_template(category_name, kwargs)
    return PageContent(
        page_name=page_names.component_family_page(project_name, component_name),
        wikitext=body + "\n",
    )


def render_versioned_component(
    file: WikiYmlFile,
    project_name: str,
    version: str,
    tag: str,
    repository_url: str | None,
    schema: Schema,
) -> PageContent:
    """Immutable per-version Component snapshot."""
    category_name = kind_to_category_name(file.kind or "")
    category = schema.categories[category_name]
    component_name = file.content["name"]

    kwargs = _content_kwargs(file, category)
    kwargs["has_name"] = component_name
    kwargs["has_project"] = project_name
    kwargs["has_version"] = version
    kwargs["has_family"] = page_names.component_family_page(project_name, component_name)

    # If the writer can compute a tag-pinned design-file URL, do so.
    if repository_url and (source_path := file.content.get("source_path")):
        kwargs["has_design_file_url"] = f"{repository_url.rstrip('/')}/tree/{tag}/{source_path}"

    body_parts = [render_template(category_name, kwargs)]
    # TODO: emit Has spec / Has BOM item subobject templates once the SemanticSchemas
    # subobject-template naming convention is confirmed against a live wiki.
    if extras := _free_text_sections(file):
        body_parts.append(extras)

    return PageContent(
        page_name=page_names.versioned_component_page(project_name, component_name, version),
        wikitext="\n\n".join(body_parts) + "\n",
        immutable=True,
    )


def render_release(
    project_file: WikiYmlFile,
    tag: str,
    component_pages: list[str],
    *,
    release_date: str,
    changelog: str | None = None,
    artifact_url: str | None = None,
    schema: Schema,
) -> PageContent:
    """Immutable per-tag Release manifest page."""
    category = schema.categories["Release"]
    project_name = project_file.content["name"]
    version = page_names.normalize_version(tag)

    kwargs: dict[str, Any] = {
        "has_name": f"{project_name} Release {version}",
        "has_version": version,
        "has_tag": tag,
        "has_project": project_name,
        "has_release_date": release_date,
        "has_component": component_pages,
    }
    if changelog:
        kwargs["has_changelog"] = changelog
    if artifact_url:
        kwargs["has_artifact_url"] = artifact_url

    # Drop any kwargs whose property isn't actually installed on this destination wiki
    field_lower = {f.name.lower() for f in category.property_fields}
    kwargs = {
        k: v
        for k, v in kwargs.items()
        if ("Has " + k[len("has_"):].replace("_", " ")).lower() in field_lower
    }

    return PageContent(
        page_name=page_names.release_page(project_name, tag),
        wikitext=render_template("Release", kwargs) + "\n",
        immutable=True,
    )
