"""Parse SemanticSchemas-style wikitext for Category and Property pages
into the schema dataclasses defined in :mod:`wiki_repo_bridge.schema`.

Two Category page formats are supported:

1. *Wiki-rendered form* — the format SemanticSchemas pages actually use on the wiki:
   a top-level ``{{Category|...}}`` with metadata, followed by repeated
   ``{{Property field/subobject|for_property=...|is_required=Yes/No}}`` and/or
   ``{{Property field/subobject|for_category=...|is_required=Yes/No}}`` invocations.

2. *Compact form* — ``has_required_property=A, B, C`` and ``has_optional_property=D, E``
   inside a single ``{{Category|...}}`` block. This is what the ``labki-ontology`` repo
   uses; the bridge accepts it for round-trip convenience but the wiki form is canonical.

Property pages use a single ``{{Property|...}}`` block in either source.
"""

from __future__ import annotations

import mwparserfromhell as mwp

from wiki_repo_bridge.schema import (
    CategoryDef,
    PropertyDef,
    PropertyField,
    SubobjectField,
)

_TRUE_VALUES = {"yes", "true", "1"}
_FALSE_VALUES = {"no", "false", "0"}


def _normalize_property_name(raw: str) -> str:
    """Strip ``Property:`` prefix and surrounding whitespace from a property reference."""
    raw = raw.strip()
    if raw.startswith("Property:"):
        raw = raw[len("Property:") :]
    return raw.strip()


def _normalize_category_name(raw: str) -> str:
    """Strip ``Category:`` prefix and surrounding whitespace from a category reference."""
    raw = raw.strip()
    if raw.startswith("Category:"):
        raw = raw[len("Category:") :]
    return raw.strip()


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"Could not interpret {raw!r} as a boolean")


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _template_param(template, key: str) -> str | None:
    """Return the stripped value of a template parameter, or None if absent/empty.

    Wiki templates accept slightly varying parameter spellings (snake_case vs spaces);
    we normalize on lookup so callers can use canonical snake_case keys.
    """
    candidates = {key, key.replace("_", " ")}
    for cand in candidates:
        if template.has(cand):
            value = str(template.get(cand).value).strip()
            return value or None
    return None


def parse_property(wikitext: str, name: str) -> PropertyDef:
    """Parse a Property page's wikitext into a :class:`PropertyDef`."""
    code = mwp.parse(wikitext)
    templates = [t for t in code.filter_templates() if t.name.strip() == "Property"]
    if not templates:
        raise ValueError(f"No {{{{Property}}}} block found in wikitext for {name!r}")
    tpl = templates[0]

    multi = _template_param(tpl, "allows_multiple_values")
    enum_values = _template_param(tpl, "allows_value")
    return PropertyDef(
        name=name,
        description=_template_param(tpl, "has_description"),
        type=_template_param(tpl, "has_type"),
        display_label=_template_param(tpl, "display_label"),
        allows_multiple_values=_parse_bool(multi) if multi else False,
        allows_value=_split_csv(enum_values) if enum_values else [],
        allows_value_from_category=_template_param(tpl, "allows_value_from_category"),
    )


def parse_category(wikitext: str, name: str) -> CategoryDef:
    """Parse a Category page's wikitext into a :class:`CategoryDef`.

    Accepts both the wiki-rendered form (separate ``Property field/subobject``
    invocations) and the compact form (``has_required_property=...`` lists).
    """
    code = mwp.parse(wikitext)
    templates = list(code.filter_templates())

    category_tpl = next((t for t in templates if t.name.strip() == "Category"), None)
    if category_tpl is None:
        raise ValueError(f"No {{{{Category}}}} block found in wikitext for {name!r}")

    cat = CategoryDef(
        name=name,
        description=_template_param(category_tpl, "has_description"),
        display_label=_template_param(category_tpl, "display_label"),
        parent_category=_template_param(category_tpl, "has_parent_category"),
        show_backlinks_for=_template_param(category_tpl, "show_backlinks_for"),
        target_namespace=_template_param(category_tpl, "has_target_namespace"),
    )

    # Wiki-rendered form: explicit Property field/subobject invocations.
    field_tpls = [
        t
        for t in templates
        if t.name.strip() in {"Property field/subobject", "Property field", "Subobject field"}
    ]
    for tpl in field_tpls:
        required_raw = _template_param(tpl, "is_required")
        required = _parse_bool(required_raw) if required_raw else False

        for_property = _template_param(tpl, "for_property")
        for_category = _template_param(tpl, "for_category")

        if for_property:
            cat.property_fields.append(
                PropertyField(name=_normalize_property_name(for_property), required=required)
            )
        elif for_category:
            cat.subobject_fields.append(
                SubobjectField(
                    target_category=_normalize_category_name(for_category), required=required
                )
            )

    # Compact form (labki-ontology repo style): comma-separated lists in the Category block.
    if not field_tpls:
        for compact_key, required in (
            ("has_required_property", True),
            ("has_optional_property", False),
        ):
            value = _template_param(category_tpl, compact_key)
            if not value:
                continue
            for prop in _split_csv(value):
                cat.property_fields.append(PropertyField(name=prop, required=required))
        for compact_key, required in (
            ("has_required_subobject", True),
            ("has_optional_subobject", False),
        ):
            value = _template_param(category_tpl, compact_key)
            if not value:
                continue
            for sub in _split_csv(value):
                cat.subobject_fields.append(
                    SubobjectField(target_category=sub, required=required)
                )

    return cat
