"""Pure helpers for emitting MediaWiki + SemanticSchemas template wikitext.

The page-instance format the bridge writes looks like::

    {{Hardware component
    |has_name=Housing
    |has_version=1.0.2
    |has_description=3D printed body...
    }}
    {{BOM Item/subobject
    |has_item=Some Part
    |has_quantity=2
    |has_unit=ea
    }}

These helpers do the rendering only — schema-driven property selection lives in
:mod:`wiki_repo_bridge.pages`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


def _format_value(value: object) -> str:
    """Convert a Python value into a wikitext-safe string for a template parameter.

    Lists become comma-separated; bools become ``Yes``/``No`` (matching the SemanticSchemas
    convention for ``is_required`` and friends); everything else gets ``str()``-ified and
    has its newlines collapsed to keep template invocations one-line per parameter.
    """
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_format_value(v) for v in value)
    return str(value).replace("\n", " ").strip()


def render_template(name: str, params: Mapping[str, object]) -> str:
    """Render a top-level template invocation.

    Empty/None values are omitted so the rendered wikitext stays clean. Parameter order
    is preserved from the input mapping (Python 3.7+ dict insertion order), so callers
    decide the visible field order on the page.
    """
    lines = ["{{" + name]
    for key, value in params.items():
        if value is None:
            continue
        rendered = _format_value(value)
        if rendered == "":
            continue
        lines.append(f"|{key}={rendered}")
    lines.append("}}")
    return "\n".join(lines)


def render_subobject(category_name: str, params: Mapping[str, object]) -> str:
    """Render a subobject-instance template invocation (``{{<Category>/subobject|...}}``).

    SemanticSchemas places subobject instances on a parent page using a template named
    after the subobject's host Category with ``/subobject`` appended.
    """
    return render_template(f"{category_name}/subobject", params)


def render_section(heading: str, body: str, level: int = 2) -> str:
    """Render a wiki section with a heading and body."""
    marks = "=" * level
    return f"{marks} {heading} {marks}\n{body.rstrip()}"


def render_bullet_list(items: Sequence[object]) -> str:
    """Render a sequence as a wiki bullet list."""
    return "\n".join(f"* {_format_value(item)}" for item in items)


# Marker-delimited managed sections let humans edit a page outside the markers
# while the bridge owns the wikitext between them. The marker text is matched
# verbatim, so don't change it after pages are deployed.
MANAGED_START = "<!-- wiki-repo-bridge Start -->"
MANAGED_END = "<!-- wiki-repo-bridge End -->"


def wrap_managed(body: str) -> str:
    """Wrap ``body`` with start/end markers so the bridge can find and replace it later."""
    return f"{MANAGED_START}\n{body.rstrip()}\n{MANAGED_END}"


def has_managed_block(wikitext: str) -> bool:
    """Whether ``wikitext`` already contains a wiki-repo-bridge managed block."""
    return MANAGED_START in wikitext and MANAGED_END in wikitext


def replace_managed_block(existing: str, new_body: str) -> str:
    """Replace the content between markers in ``existing`` with ``new_body``.

    Raises ``ValueError`` if markers aren't found or aren't in start/end order.
    """
    start = existing.find(MANAGED_START)
    end = existing.find(MANAGED_END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("managed-block markers not found or out of order")
    before = existing[:start]
    after = existing[end + len(MANAGED_END):]
    return f"{before}{wrap_managed(new_body)}{after}"


_HAS_VERSION_RE = re.compile(r"\|\s*has_version\s*=\s*([^\n|}]+)")


def parse_managed_version(wikitext: str) -> str | None:
    """Extract the ``has_version=...`` value from the managed block of an existing page.

    Returns the trimmed value or ``None`` if no managed block / no has_version found.
    Used by the version-bump flow to decide whether to archive before writing.
    """
    if not has_managed_block(wikitext):
        return None
    start = wikitext.find(MANAGED_START)
    end = wikitext.find(MANAGED_END)
    block = wikitext[start:end]
    m = _HAS_VERSION_RE.search(block)
    return m.group(1).strip() if m else None


def semver_tuple(version: str) -> tuple[int, ...]:
    """Parse ``1.2.3`` or ``v1.2.3`` to ``(1, 2, 3)`` for comparison.

    Pre-release/build metadata after ``-`` or ``+`` is dropped — proper pre-release
    comparison is more complex than the bridge needs. Raises ``ValueError`` on non-semver.
    """
    s = version[1:] if version.startswith("v") else version
    s = s.split("-", 1)[0].split("+", 1)[0]
    parts = s.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Not a semver-formatted version: {version!r}")
    return tuple(int(p) for p in parts)
