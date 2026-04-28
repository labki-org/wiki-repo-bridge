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
from wiki_repo_bridge.readme import ReadmeContent
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
    render_subobject,
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
    """For Component pages: the version this rendering reflects. Recorded on the
    PageContent so the executor can log per-version writes."""

    redirect_target: str | None = None
    """When set, the page is written as a pure ``#REDIRECT [[<target>]]`` and overwrites
    any existing content. Used for the canonical Component page → current version redirect."""

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


def _free_text_sections(
    file: WikiYmlFile, *, repository_url: str | None = None, tag: str | None = None,
) -> str:
    """Render any free-form structural blocks (features, design_files) as wiki sections.

    When ``repository_url`` and ``tag`` are both provided, ``design_files`` paths render
    as external links to the tagged blob URL (e.g. ``github.com/.../blob/v0.1.0/...``)
    so people clicking from the wiki land on the *exact* version that release describes.
    """
    sections: list[str] = []
    if features := file.content.get("features"):
        if isinstance(features, list) and features:
            sections.append(render_section("Features", render_bullet_list(features)))
    if design_files := file.content.get("design_files"):
        if isinstance(design_files, dict):
            base_path = file.content.get("source_path") or _component_dir(file)
            lines = []
            for label, value in design_files.items():
                pretty_label = label.replace("_", " ")
                if isinstance(value, list):
                    lines.append(f"* '''{pretty_label}''':")
                    lines.extend(
                        f"** {_design_file_link(v, repository_url, tag, base_path)}"
                        for v in value
                    )
                else:
                    lines.append(
                        f"* '''{pretty_label}''': "
                        f"{_design_file_link(value, repository_url, tag, base_path)}"
                    )
            sections.append(render_section("Design Files", "\n".join(lines)))
    return "\n\n".join(sections)


def _component_dir(file: WikiYmlFile) -> str | None:
    """Component dir relative to the repo root, derived from where the wiki.yml lives."""
    parent = file.relative_path.parent
    return str(parent) if str(parent) not in ("", ".") else None


def _design_file_link(
    value: object, repository_url: str | None, tag: str | None, base_path: str | None,
) -> str:
    """Render one design-file entry as a tagged-URL link when possible, else plain text."""
    text = str(value)
    if not repository_url or not tag:
        return text
    # Skip values that are already URLs or look directory-like — leave to author's intent.
    if text.startswith(("http://", "https://", "/")):
        return text
    rel = f"{base_path}/{text}" if base_path else text
    return f"[{page_names.repo_blob_url(repository_url, tag, rel)} {text}]"


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


DEFAULT_PROJECT_STATUS = "Active"


def _specs_subobjects(file: WikiYmlFile) -> str:
    """Render ``specs:`` entries as ``{{Specification/subobject|...}}`` invocations.

    Each entry maps ``name`` / ``value`` / ``unit`` keys to the corresponding
    ``has_name`` / ``has_value`` / ``has_unit`` template parameters. Entries with
    only some fields render those fields and omit the rest.
    """
    specs = file.content.get("specs")
    if not isinstance(specs, list) or not specs:
        return ""
    parts = []
    for entry in specs:
        if not isinstance(entry, dict):
            continue
        kwargs = {}
        for yaml_key, param in (("name", "has_name"), ("value", "has_value"),
                                ("unit", "has_unit")):
            if (v := entry.get(yaml_key)) not in (None, ""):
                kwargs[param] = v
        if kwargs:
            parts.append(render_subobject("Specification", kwargs))
    return "\n".join(parts)


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

    project_name = file.content["name"]
    repository_url = file.content.get("repository_url")
    managed_parts = [main]
    if specs_block := _specs_subobjects(file):
        managed_parts.append(specs_block)
    if extras := _free_text_sections(file, repository_url=repository_url, tag=None):
        managed_parts.append(extras)
    if images_block := _images_section(images or []):
        managed_parts.append(images_block)
    managed_body = "\n\n".join(managed_parts)

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
    readme: ReadmeContent | None = None,
) -> PageContent:
    """Per-version Component page in managed-section mode.

    Lives at ``<Project>/Component/<Name>/v<version>`` and carries the dispatcher
    template (with this version's project link, design-file URL, and source path)
    plus specs, design files, images, and README. The canonical name (no version)
    is a separate redirect page produced by :func:`render_component_redirect`.
    """
    category_name = kind_to_category_name(file.kind or "")
    category = schema.categories[category_name]
    component_name = file.content["name"]

    kwargs = _content_kwargs(file, category)
    kwargs["has_name"] = component_name
    kwargs["has_project"] = project_name
    kwargs["has_version"] = version
    if repository_url and (source_path := file.content.get("source_path")):
        kwargs["has_design_file_url"] = page_names.repo_tree_url(
            repository_url, tag, source_path,
        )

    managed_parts = [render_template(category_name, kwargs)]
    if specs_block := _specs_subobjects(file):
        managed_parts.append(specs_block)
    if extras := _free_text_sections(file, repository_url=repository_url, tag=tag):
        managed_parts.append(extras)
    if images_block := _images_section(images or []):
        managed_parts.append(images_block)
    if readme is not None:
        managed_parts.append(render_section("README", readme.wikitext))
    managed_body = "\n\n".join(managed_parts)

    return PageContent(
        page_name=page_names.component_versioned_page(project_name, component_name, version),
        managed_body=managed_body,
        scaffold=f"= {component_name} {version} =\n",
        version=version,
    )


def render_component_redirect(
    project_name: str, component_name: str, version: str,
) -> PageContent:
    """Canonical Component page redirecting to the current versioned subpage.

    Overwritten on every sync so the canonical name always points at the latest
    release. SMW resolves property values pointing at the canonical name through
    the redirect, so ``Release.Has component=[[<Project>/Component/<Name>]]`` works
    naturally — but the Release page itself links to the versioned subpage to keep
    the per-version snapshot stable.
    """
    return PageContent(
        page_name=page_names.component_page(project_name, component_name),
        redirect_target=page_names.component_versioned_page(
            project_name, component_name, version,
        ),
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
    readme: ReadmeContent | None = None,
) -> PageContent:
    """Immutable per-tag Release manifest page.

    ``images`` is the list of *project-level* image uploads (not component-level —
    those live on the Component pages). The Release page references them via
    ``Has image`` only; visual rendering of versioned thumbnails is left off the
    Release page since the queryable annotation is what matters.

    ``readme`` is the project root README converted to wikitext, snapshotted on this
    immutable page so each release captures its own README state.
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
    if readme is not None:
        body_parts.append(render_section("README", readme.wikitext))

    return PageContent(
        page_name=page_names.release_page(project_name, tag),
        wikitext="\n\n".join(body_parts) + "\n",
        immutable=True,
    )
