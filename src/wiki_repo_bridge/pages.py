"""Build the wikitext for each kind of page the bridge writes.

There are three shapes:

* **Component page** — canonical page for a component, always reflecting the latest
  version. CI owns the wikitext between ``<!-- wiki-repo-bridge Start/End -->`` markers;
  humans own everything outside. On version bumps the previous page is moved to a
  ``/v<old>`` subpage so its history is preserved as an archive.
* **Project page** — same managed-section pattern as Component pages.
* **Release** — immutable per-tag manifest bundling the per-version component snapshots.

Each renderer takes the parsed wiki.yml file plus context (project name, tag, schema)
and returns a :class:`PageContent` describing what to write where.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wiki_repo_bridge import page_names
from wiki_repo_bridge.images import ImageUpload, render_image_thumb
from wiki_repo_bridge.schema import CategoryDef, Schema
from wiki_repo_bridge.validator import (
    STRUCTURAL_KEYS,
    kind_to_category_name,
    property_name_to_param,
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
    """One page the bridge intends to write.

    There are three write modes, chosen by which fields are set:

    * ``managed_body`` set: read-modify-write between markers. On first create,
      ``scaffold`` (or empty) provides the human-editable wrapper; on re-sync the
      content between markers is replaced with a freshly-rendered ``managed_body``.
    * ``immutable=True``: write once, skip if the page already exists.
    * ``bootstrap_only=True``: write once if absent; never overwrite.

    Plain ``wikitext`` mode (no flags, no managed_body) overwrites unconditionally.
    """

    page_name: str
    wikitext: str = ""
    """Full page content for plain/immutable/bootstrap pages."""

    managed_body: str | None = None
    """Wikitext to place between markers. When set, scaffold + markers is used on first
    create and a read-modify-write replaces just the marker block on subsequent syncs."""

    scaffold: str = ""
    """Text written outside the markers on first create. Ignored on subsequent syncs
    (humans own what's outside the markers from then on)."""

    version: str | None = None
    """For Component pages: the version this rendering reflects. The executor compares
    against the version on the existing wiki page; on a bump the existing page is moved
    to a /v<old> archive subpage before the new content is written."""

    immutable: bool = False
    """Skip if the page already exists. Used for Release pages."""

    bootstrap_only: bool = False
    """Write once if absent; never overwrite. Mostly subsumed by managed_body."""


def _filter_to_installed(
    kwargs: dict[str, Any], category: CategoryDef
) -> dict[str, Any]:
    """Drop kwargs whose corresponding Property isn't installed on ``category``,
    and re-emit the survivors in field-declaration order."""
    installed_params = [property_name_to_param(f.name) for f in category.property_fields]
    ordered: dict[str, Any] = {p: kwargs[p] for p in installed_params if p in kwargs}
    return ordered


def _content_kwargs(file: WikiYmlFile, category: CategoryDef) -> dict[str, str]:
    """Map wiki.yml keys to template parameters, dropping structural keys and any
    keys whose property isn't installed on ``category``."""
    installed_props_lower = {f.name.lower() for f in category.property_fields}
    chosen: dict[str, Any] = {}
    for key, value in file.content.items():
        if key in STRUCTURAL_KEYS:
            continue
        if yaml_key_to_property_name(key).lower() not in installed_props_lower:
            continue
        chosen["has_" + key] = value
    return _filter_to_installed(chosen, category)


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


def _images_section(uploads: list[ImageUpload]) -> str:
    """Render an Images section using each upload's *alias* (unversioned) filename
    so the Component/Project page always points at the latest binaries.

    Also emits ``[[Has image::File:...]]`` SMW annotations so other wiki pages can
    query a component's images via SMW.
    """
    if not uploads:
        return ""
    thumbs = [render_image_thumb(u.alias_name, caption=u.caption) for u in uploads]
    annotations = "\n".join(f"[[Has image::File:{u.alias_name}]]" for u in uploads)
    body = "<gallery mode=\"packed\">\n"
    body += "\n".join(
        f"{u.alias_name}|{u.caption}" if u.caption else u.alias_name for u in uploads
    )
    body += "\n</gallery>\n\n"
    body += annotations
    # Use thumbs only when the gallery isn't expressive enough; for a single image,
    # a thumb on its own reads better than a 1-element gallery.
    if len(uploads) == 1:
        u = uploads[0]
        return render_section("Images", f"{thumbs[0]}\n\n[[Has image::File:{u.alias_name}]]")
    return render_section("Images", body)


DEFAULT_PROJECT_STATUS = "active"


def render_project(
    file: WikiYmlFile, schema: Schema, *, images: list[ImageUpload] | None = None,
) -> PageContent:
    """Project page in managed-section mode.

    The CI-owned block carries the dispatcher template and any free-form sections
    derived from wiki.yml. On first create the bridge writes a thin scaffold above
    the markers; humans then own everything outside the markers.
    """
    category = schema.categories["Project"]
    kwargs = _content_kwargs(file, category)
    kwargs.setdefault("has_project_status", DEFAULT_PROJECT_STATUS)
    main = render_template("Project", _filter_to_installed(kwargs, category))

    managed_parts = [main]
    if extras := _free_text_sections(file):
        managed_parts.append(extras)
    if images_block := _images_section(images or []):
        managed_parts.append(images_block)
    managed_body = "\n\n".join(managed_parts)

    project_name = file.content["name"]
    return PageContent(
        page_name=page_names.project_page(project_name),
        managed_body=managed_body,
        scaffold=f"= {project_name} =\n",
    )


def render_component(
    file: WikiYmlFile,
    project_name: str,
    version: str,
    tag: str,
    repository_url: str | None,
    schema: Schema,
    *,
    images: list[ImageUpload] | None = None,
) -> PageContent:
    """Component page in managed-section mode — always reflects the latest version.

    The CI-owned block carries the dispatcher template (with current version,
    project link, and design-file URL) plus any free-form sections. The previous
    version's content gets archived to ``/v<old>`` by the sync flow on bumps.
    """
    category_name = kind_to_category_name(file.kind or "")
    category = schema.categories[category_name]
    component_name = file.content["name"]

    kwargs = _content_kwargs(file, category)
    kwargs["has_name"] = component_name
    kwargs["has_project"] = project_name
    kwargs["has_version"] = version
    if repository_url and (source_path := file.content.get("source_path")):
        kwargs["has_design_file_url"] = f"{repository_url.rstrip('/')}/tree/{tag}/{source_path}"

    managed_parts = [render_template(category_name, kwargs)]
    if extras := _free_text_sections(file):
        managed_parts.append(extras)
    if images_block := _images_section(images or []):
        managed_parts.append(images_block)
    managed_body = "\n\n".join(managed_parts)

    return PageContent(
        page_name=page_names.component_page(project_name, component_name),
        managed_body=managed_body,
        scaffold=f"= {component_name} =\n",
        version=version,
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
    images: list[ImageUpload] | None = None,
) -> PageContent:
    """Immutable per-tag Release manifest page.

    ``images`` is the full list of image uploads (project + component) for the release.
    The Release page links to each image's *versioned* filename, freezing the manifest
    at the moment of the tag — even after the unversioned alias is overwritten by a
    later release, the Release page still points at this release's binaries.
    """
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
    if responsible_party := project_file.content.get("responsible_party"):
        kwargs["has_responsible_party"] = responsible_party
    if images:
        kwargs["has_image"] = [f"File:{u.versioned_name}" for u in images]

    kwargs = _filter_to_installed(kwargs, category)
    body_parts = [render_template("Release", kwargs)]
    if images:
        # Versioned thumbs in a gallery — the binary at this name is immutable across releases.
        gallery_lines = []
        for u in images:
            line = u.versioned_name
            if u.caption:
                line += f"|{u.caption}"
            gallery_lines.append(line)
        gallery = "<gallery mode=\"packed\">\n" + "\n".join(gallery_lines) + "\n</gallery>"
        body_parts.append(render_section("Images", gallery))

    return PageContent(
        page_name=page_names.release_page(project_name, tag),
        wikitext="\n\n".join(body_parts) + "\n",
        immutable=True,
    )
