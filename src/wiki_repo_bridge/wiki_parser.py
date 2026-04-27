"""Parse SemanticSchemas-style wikitext for Category and Property pages
into the schema dataclasses defined in :mod:`wiki_repo_bridge.schema`.

The wiki has two formats in active use:

1. *Dispatcher form* — the canonical SemanticSchemas form, used by every typical
   Category and Property page (and every page we'll create going forward):
   ``{{Category|...}}`` / ``{{Property|...}}`` template invocations that the
   ``Category`` and ``Property`` dispatcher templates expand into the right SMW
   annotations. Categories carry their fields as separate
   ``{{Property field/subobject|for_property=...|is_required=Yes/No}}`` calls.

2. *Raw SMW form* — used by a handful of bootstrap pages (e.g. ``Has description``,
   ``Category:Category``) that the dispatcher templates themselves depend on, and
   so cannot use the dispatcher to define themselves. Raw ``[[Has X::value]]``
   annotations and ``{{#subobject:|@category=Property field|For property=...}}``.

The parser tries dispatcher form first and falls back to raw SMW for bootstrap pages.

The ``labki-ontology`` repo also uses a *compact form* in its source files
(``has_required_property=A, B, C`` lists inside a single ``{{Category|...}}`` block).
The Category parser accepts that too for round-trip convenience.
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
    return raw.strip().removeprefix("Property:").strip()


def _normalize_category_name(raw: str) -> str:
    """Strip ``Category:`` prefix and surrounding whitespace from a category reference."""
    return raw.strip().removeprefix("Category:").strip()


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


_META_CATEGORY_MARKERS: tuple[str, ...] = (
    "managed",  # OntologySync-managed, SemanticSchemas-managed, etc.
)


def _detect_parent_from_category_links(code) -> str | None:
    """Find a parent Category from ``[[Category:X]]`` markers, skipping meta-categories
    like ``OntologySync-managed`` / ``SemanticSchemas-managed``."""
    for link in code.filter_wikilinks():
        title = str(link.title).strip()
        if not title.startswith("Category:"):
            continue
        target = title[len("Category:"):].strip()
        lower = target.lower()
        if any(marker in lower for marker in _META_CATEGORY_MARKERS):
            continue
        return target
    return None


def _extract_smw_annotations(code) -> dict[str, str]:
    """Pull every ``[[Property::Value]]`` semantic annotation out of wikitext.

    The key is normalized to snake_case (``Has type`` → ``has_type``,
    ``Display label`` → ``display_label``) so it lines up with the snake_case
    parameter names used by the helper-template form.
    """
    annotations: dict[str, str] = {}
    for link in code.filter_wikilinks():
        title = str(link.title).strip()
        if "::" not in title:
            continue
        prop, _, value = title.partition("::")
        prop = prop.strip()
        value = value.strip()
        # Skip [[Category:Foo]] markers (they have no '::')
        if not prop or prop.startswith("Category:"):
            continue
        key = prop.replace(" ", "_").lower()
        annotations[key] = value
    return annotations


def _property_source_from_template(code) -> dict[str, str] | None:
    """If ``{{Property|...}}`` is present, return its parameters as a dict."""
    templates = [t for t in code.filter_templates() if t.name.strip() == "Property"]
    if not templates:
        return None
    tpl = templates[0]
    keys = ["has_description", "has_type", "display_label",
            "allows_multiple_values", "allows_value", "allows_value_from_category"]
    return {k: v for k in keys if (v := _template_param(tpl, k)) is not None}


def parse_property(wikitext: str, name: str) -> PropertyDef:
    """Parse a Property page into a :class:`PropertyDef`.

    The canonical wiki form is the ``{{Property|...}}`` dispatcher template;
    bootstrap pages (``Has description`` etc.) use raw ``[[Has X::Y]]`` SMW
    annotations because the dispatcher itself depends on them.
    """
    code = mwp.parse(wikitext)
    source = _property_source_from_template(code) or _extract_smw_annotations(code)
    if not source:
        raise ValueError(f"No Property data found in wikitext for {name!r}")

    multi = source.get("allows_multiple_values")
    enum_values = source.get("allows_value")
    return PropertyDef(
        name=name,
        description=source.get("has_description"),
        type=source.get("has_type"),
        display_label=source.get("display_label"),
        allows_multiple_values=_parse_bool(multi) if multi else False,
        allows_value=_split_csv(enum_values) if enum_values else [],
        allows_value_from_category=source.get("allows_value_from_category"),
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

    parent = _template_param(category_tpl, "has_parent_category")
    if parent is None:
        # Fall back to [[Category:X]] markers — that's how the wiki itself encodes
        # the parent of e.g. Category:Hardware component → Category:Component.
        parent = _detect_parent_from_category_links(code)

    cat = CategoryDef(
        name=name,
        description=_template_param(category_tpl, "has_description"),
        display_label=_template_param(category_tpl, "display_label"),
        parent_category=parent,
        show_backlinks_for=_template_param(category_tpl, "show_backlinks_for"),
        target_namespace=_template_param(category_tpl, "has_target_namespace"),
    )

    # Wiki-rendered form: explicit Property/Subobject field/subobject invocations.
    field_tpls = [
        t
        for t in templates
        if t.name.strip() in {
            "Property field/subobject",
            "Subobject field/subobject",
            "Property field",
            "Subobject field",
        }
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
