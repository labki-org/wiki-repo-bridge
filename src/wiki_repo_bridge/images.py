"""Image declaration, upload-name generation, and wikitext-snippet rendering.

Each ``wiki.yml`` (project root or component dir) may declare an ``images:`` list::

    images:
      - path: assets/render.png
        caption: Assembled baseplate
        kind: render          # photo | render | schematic | plot | other

Paths are resolved relative to the declaring wiki.yml's directory and must not
escape it. Each declared image produces two uploads on the destination wiki:

* a versioned name ``{Project}_{Component}_v{V}_{stem}.{ext}`` — immutable history
* an unversioned alias ``{Project}_{Component}_{stem}.{ext}`` — overwritten each
  release; gives consumers a stable name for the latest

Project-level images (declared in the root wiki.yml) drop the component segment
and use the project tag version.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from wiki_repo_bridge.page_names import normalize_version
from wiki_repo_bridge.walker import WikiYmlFile


@dataclass(frozen=True)
class ImageDeclaration:
    """A single ``images:`` entry resolved to an absolute on-disk path."""

    abs_path: Path
    """Absolute path to the image file on disk."""

    caption: str = ""
    kind: str = ""

    @property
    def stem(self) -> str:
        return self.abs_path.stem

    @property
    def suffix(self) -> str:
        return self.abs_path.suffix.lstrip(".").lower()


@dataclass(frozen=True)
class ImageUpload:
    """One file the bridge intends to upload (under both versioned + alias names)."""

    abs_path: Path
    versioned_name: str
    """Wiki filename including the version, e.g. ``MiniXL_Baseplate_v0.1.0_render.png``."""

    alias_name: str
    """Unversioned wiki filename, overwritten on each release."""

    caption: str = ""
    kind: str = ""


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _slug(text: str) -> str:
    """Make a filename-safe segment: collapse non-alphanumerics into ``_``."""
    return _SLUG_RE.sub("_", text).strip("_")


def discover_images(
    file: WikiYmlFile, *, repo_root: Path
) -> tuple[list[ImageDeclaration], list[str]]:
    """Resolve ``images:`` entries on a wiki.yml, returning (declarations, errors).

    Errors are returned (not raised) so the validator can collect them per file
    alongside other issues.
    """
    declarations: list[ImageDeclaration] = []
    errors: list[str] = []
    raw = file.content.get("images")
    if raw is None:
        return declarations, errors
    if not isinstance(raw, list):
        errors.append("images: must be a list")
        return declarations, errors

    base_dir = file.path.parent.resolve()
    repo_root_abs = Path(repo_root).resolve()

    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or "path" not in entry:
            errors.append(f"images[{i}]: must be a mapping with at least a 'path' key")
            continue
        rel_path = str(entry["path"])
        # Reject paths that escape the wiki.yml's directory before they hit the FS.
        if rel_path.startswith("/") or ".." in Path(rel_path).parts:
            errors.append(f"images[{i}]: path {rel_path!r} must stay inside {base_dir.name}/")
            continue
        abs_path = (base_dir / rel_path).resolve()
        try:
            abs_path.relative_to(base_dir)
        except ValueError:
            errors.append(f"images[{i}]: path {rel_path!r} resolves outside {base_dir.name}/")
            continue
        if not abs_path.is_file():
            try:
                shown = abs_path.relative_to(repo_root_abs)
            except ValueError:
                shown = abs_path
            errors.append(f"images[{i}]: file not found at {shown}")
            continue
        declarations.append(
            ImageDeclaration(
                abs_path=abs_path,
                caption=str(entry.get("caption", "")),
                kind=str(entry.get("kind", "")),
            )
        )
    return declarations, errors


def wiki_filename(
    *, project: str, component: str | None, version: str, stem: str, suffix: str
) -> str:
    """Build the versioned wiki filename for an image.

    ``component`` is ``None`` for project-level images.
    """
    parts = [_slug(project)]
    if component:
        parts.append(_slug(component))
    parts.append(f"v{normalize_version(version)}")
    parts.append(_slug(stem))
    return f"{'_'.join(parts)}.{suffix}"


def alias_filename(
    *, project: str, component: str | None, stem: str, suffix: str
) -> str:
    """Build the unversioned alias filename — overwritten each release."""
    parts = [_slug(project)]
    if component:
        parts.append(_slug(component))
    parts.append(_slug(stem))
    return f"{'_'.join(parts)}.{suffix}"


def render_image_thumb(
    wiki_name: str, *, caption: str = "", width_px: int = 300
) -> str:
    """Render ``[[File:...|thumb|right|<width>px|<caption>]]`` for a managed body."""
    parts = [f"File:{wiki_name}", "thumb", "right", f"{width_px}px"]
    if caption:
        parts.append(caption)
    return "[[" + "|".join(parts) + "]]"


def file_sha1(abs_path: Path) -> str:
    """Compute the SHA-1 hex digest of a file — for upload dedup against the wiki."""
    h = hashlib.sha1()
    with open(abs_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
