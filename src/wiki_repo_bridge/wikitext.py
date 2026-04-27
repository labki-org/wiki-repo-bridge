"""Pure helpers for emitting MediaWiki + SemanticSchemas template wikitext.

The page-instance format the bridge writes looks like::

    {{Hardware Component
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
