"""Read README.md files next to a wiki.yml and convert them to MediaWiki wikitext.

Convention: any directory with a ``wiki.yml`` may have a sibling ``README.md`` whose
contents get embedded under a ``== README ==`` section on the generated wiki page.
Component READMEs land on the Component page (inside the managed block, so wiki
edits outside the markers are still preserved). The project-root README lands on
the immutable Release page so each release captures a snapshot.

Conversion uses ``pypandoc`` with the GFM input dialect for richer markdown support
(fenced code blocks, tables). After conversion we:

* strip the empty ``<span id="...">`` heading anchors pandoc emits (wiki has no
  use for HTML anchors and they read as visual noise)
* drop any YAML frontmatter from the source before conversion
* rewrite ``[[File:<local-path>|...]]`` references to the wiki File: alias name
  whenever the local path matches a declared image
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from wiki_repo_bridge.images import ImageDeclaration, ImageUpload, alias_filename

log = logging.getLogger(__name__)

README_FILENAME = "README.md"
README_SIZE_WARN_BYTES = 50_000  # ~50 KB — flag oversized READMEs without blocking


@dataclass(frozen=True)
class ReadmeContent:
    """A README ready to be embedded under ``== README ==``."""

    wikitext: str
    """Converted wikitext, no surrounding section heading."""

    source_path: Path
    """The README.md file the wikitext came from."""


_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_HEADING_ANCHOR_RE = re.compile(r'^<span id="[^"]*"></span>\s*\n', re.MULTILINE)


def discover_readme(wiki_yml_dir: Path) -> Path | None:
    """Return the ``README.md`` next to a ``wiki.yml`` (case-sensitive), or None."""
    candidate = wiki_yml_dir / README_FILENAME
    return candidate if candidate.is_file() else None


def _strip_frontmatter(md: str) -> str:
    return _FRONTMATTER_RE.sub("", md, count=1) if md.startswith("---") else md


def _strip_heading_anchors(wikitext: str) -> str:
    return _HEADING_ANCHOR_RE.sub("", wikitext)


_ABSOLUTE_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MD_LINK_RE = re.compile(r"(?<!\!)\[([^\]]+)\]\(([^)]+)\)")


def _is_absolute_url(s: str) -> bool:
    return bool(_ABSOLUTE_URL_RE.match(s))


def _rewrite_md_links_to_absolute(
    md: str,
    path_to_alias: dict[str, str],
    repository_url: str | None,
    tag: str | None,
    resolve_to_repo_relative,
) -> str:
    """Pre-process the markdown so pandoc only sees absolute URLs (or known image paths).

    Why pre-process: pandoc's mediawiki output collapses ``[X](X)`` into ``[[X]]``,
    indistinguishable from a user-written wikilink. By substituting absolute URLs
    *before* pandoc runs, every relative repo link comes out as a clean ``[url label]``
    external link instead. Declared images are left as relative paths so pandoc still
    emits ``[[File:path|alt]]``, which we then swap for the upload alias.
    """

    def repl_image(m: re.Match[str]) -> str:
        alt, target = m.group(1), m.group(2)
        if _is_absolute_url(target):
            return m.group(0)
        if target in path_to_alias:
            return m.group(0)  # leave declared image; alias-swap happens post-pandoc
        # Undeclared image: file isn't on the wiki, so render as a regular link to the
        # tagged blob URL. (We use blob/, not raw/, so users see the GitHub page with
        # context — switch to raw/ if anyone wants inline embedding via raw bytes.)
        if repository_url and tag:
            repo_rel = resolve_to_repo_relative(target)
            if repo_rel is not None:
                return f"[{alt}]({_tagged_blob_url(repository_url, tag, repo_rel)})"
        return m.group(0)

    def repl_link(m: re.Match[str]) -> str:
        text, target = m.group(1), m.group(2)
        if _is_absolute_url(target) or target.startswith("#"):
            return m.group(0)
        if repository_url and tag:
            repo_rel = resolve_to_repo_relative(target)
            if repo_rel is not None:
                return f"[{text}]({_tagged_blob_url(repository_url, tag, repo_rel)})"
        return m.group(0)

    md = _MD_IMAGE_RE.sub(repl_image, md)
    md = _MD_LINK_RE.sub(repl_link, md)
    return md


def _swap_declared_image_paths_for_aliases(
    wikitext: str, path_to_alias: dict[str, str]
) -> str:
    """Post-pandoc: replace ``[[File:<rel-path>|alt]]`` with ``[[File:<alias>|alt]]``."""

    def repl(m: re.Match[str]) -> str:
        path = m.group(1)
        rest = m.group(2) or ""
        alias = path_to_alias.get(path)
        return f"[[File:{alias}{rest}]]" if alias else m.group(0)

    return re.sub(r"\[\[File:([^|\]]+)(\|[^\]]*)?\]\]", repl, wikitext)


def _tagged_blob_url(repository_url: str, tag: str, rel_path: str) -> str:
    """Build a GitHub blob URL pinned to a tag for a path relative to the repo root."""
    from wiki_repo_bridge.page_names import repo_blob_url

    return repo_blob_url(repository_url, tag, rel_path)


def convert_readme(
    md_path: Path,
    *,
    images: list[ImageDeclaration] | list[ImageUpload] | None = None,
    repository_url: str | None = None,
    tag: str | None = None,
    repo_root: Path | None = None,
) -> ReadmeContent:
    """Read ``md_path`` and convert it to wikitext.

    ``images`` may be either ``ImageDeclaration``s (pre-naming) or ``ImageUpload``s
    (post-naming). When uploads are passed, image links matching their source path
    are rewritten to the upload's alias name on the wiki.

    When ``repository_url`` and ``tag`` are both provided, every relative link or image
    reference that doesn't match a declared upload is rewritten to a tagged GitHub blob
    URL — so a click from the wiki lands on the *exact* version of that file, never on
    a moving branch. Absolute URLs are left untouched.

    Raises ``ImportError`` (with a clear message) if ``pypandoc`` isn't installed.
    """
    try:
        import pypandoc
    except ImportError as e:
        raise ImportError(
            "README sync requires pypandoc — install via `pip install pypandoc-binary`"
        ) from e

    raw = md_path.read_text(encoding="utf-8")
    if len(raw) > README_SIZE_WARN_BYTES:
        log.warning(
            "%s is %d bytes (>%d KB) — wiki page will be large",
            md_path, len(raw), README_SIZE_WARN_BYTES // 1000,
        )

    readme_dir = md_path.parent
    path_to_alias = _build_path_to_alias_map(readme_dir, images or [])

    def resolve_to_repo_relative(rel_to_readme: str) -> str | None:
        """Express a README-relative path as repo-root-relative, for tagged GitHub URLs.

        Returns None if the path escapes the repo or no repo root was provided.
        """
        if repo_root is None:
            return rel_to_readme
        try:
            target = (readme_dir / rel_to_readme).resolve().relative_to(repo_root.resolve())
        except (ValueError, OSError):
            return None
        return str(target).replace("\\", "/")

    md = _strip_frontmatter(raw)
    md = _rewrite_md_links_to_absolute(
        md, path_to_alias, repository_url, tag, resolve_to_repo_relative,
    )
    wikitext = pypandoc.convert_text(md, "mediawiki", format="gfm",
                                     extra_args=["--wrap=none"])
    wikitext = _strip_heading_anchors(wikitext).strip()
    # Declared images survived pre-processing as relative paths and pandoc rendered
    # them as [[File:<rel>|alt]]; swap the path for the upload's alias name.
    if path_to_alias:
        wikitext = _swap_declared_image_paths_for_aliases(wikitext, path_to_alias)

    return ReadmeContent(wikitext=wikitext, source_path=md_path)


def _build_path_to_alias_map(
    readme_dir: Path,
    images: list[ImageDeclaration] | list[ImageUpload],
) -> dict[str, str]:
    """Map each declared image's relative-to-readme-dir path to its wiki alias name.

    Both ``ImageDeclaration`` (pre-upload) and ``ImageUpload`` (post-upload) carry an
    ``abs_path``; for declarations we have to derive the alias name from the path stem
    on-the-fly, but for uploads we use the already-computed ``alias_name``.
    """
    out: dict[str, str] = {}
    for img in images:
        abs_path = img.abs_path
        try:
            rel = abs_path.relative_to(readme_dir.resolve())
        except ValueError:
            continue
        rel_str = str(rel).replace("\\", "/")
        if isinstance(img, ImageUpload):
            out[rel_str] = img.alias_name
        else:
            # ImageDeclaration: caller didn't precompute an alias. Build one with the
            # readme dir's name as the component segment — best effort.
            out[rel_str] = alias_filename(
                project=readme_dir.name, component=None,
                stem=img.stem, suffix=img.suffix,
            )
    return out
