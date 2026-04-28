"""Validate parsed ``wiki.yml`` files against a wiki's installed Schema.

The validator does three jobs:

1. Resolve each file's ``kind`` to a Category in the schema.
2. Confirm every required property of that Category has a matching key in the wiki.yml.
3. Warn on unknown top-level keys that don't map to any known Property and aren't
   structural (``kind``, ``wiki``, ``specs``, ``bom``, ``citation``, ``features``,
   ``design_files``).

Property-value typing (URL well-formedness, Page-target existence, enum membership)
isn't checked here — that's a follow-on layer once we have a real wiki to test against.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from wiki_repo_bridge.schema import CategoryDef, Schema
from wiki_repo_bridge.walker import WikiYmlFile

# Top-level wiki.yml keys that are structural (handled by the writer/CI flow) rather than
# direct mappings to Category properties.
STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {
        "kind",         # writer dispatches on this; not a wiki property
        "wiki",         # bridge config block (e.g., base_path)
        "specs",        # subobject list, written as Has spec subobjects
        "bom",          # path to a CSV the writer parses into BOM Item subobjects
        "citation",     # generates a separate Publication page, not a Project property
        "features",     # free-form list rendered into the bootstrap stub; no property mapping
        "design_files", # free-form file list rendered as a section; no property mapping
    }
)


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Kind(StrEnum):
    """Recognized values for the top-level ``kind`` field in a wiki.yml."""

    PROJECT = "project"
    HARDWARE_COMPONENT = "hardware_component"
    SOFTWARE_COMPONENT = "software_component"
    FIRMWARE_COMPONENT = "firmware_component"
    ANALYSIS_COMPONENT = "analysis_component"

    @classmethod
    def is_component(cls, kind: str | None) -> bool:
        return kind is not None and kind != cls.PROJECT and kind in {k.value for k in cls}


# Properties the bridge auto-injects on output, so the validator doesn't require
# them in the wiki.yml. Component pages get their project from the repo's
# top-level wiki.yml; the writer synthesizes family/latest-version/design-file
# URL from context.
CI_INJECTED_BY_KIND: dict[str, frozenset[str]] = {
    Kind.PROJECT: frozenset({"Has project status"}),
    Kind.HARDWARE_COMPONENT: frozenset(
        {"Has project", "Has family", "Has latest version", "Has design file url"}
    ),
    Kind.SOFTWARE_COMPONENT: frozenset(
        {"Has project", "Has family", "Has latest version"}
    ),
    Kind.FIRMWARE_COMPONENT: frozenset(
        {"Has project", "Has family", "Has latest version"}
    ),
    Kind.ANALYSIS_COMPONENT: frozenset(
        {"Has project", "Has family", "Has latest version"}
    ),
}


def ci_injected_for_kind(kind: str | None) -> frozenset[str]:
    """Properties the bridge fills in for a given kind, exempt from validator's
    required-property check."""
    if kind is None:
        return frozenset()
    return CI_INJECTED_BY_KIND.get(kind, frozenset())


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    file: str  # relative path within the repo
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.file}: {self.message}"


def kind_to_category_name(kind: str) -> str:
    """``hardware_component`` → ``Hardware component`` (MediaWiki capitalizes
    only the first letter of a page title; subsequent words stay lowercase)."""
    s = kind.replace("_", " ")
    return s[0].upper() + s[1:] if s else s


def yaml_key_to_property_name(key: str) -> str:
    """``repository_url`` → ``Has repository url`` (wiki Property page name)."""
    return "Has " + key.replace("_", " ")


def property_name_to_param(property_name: str) -> str:
    """``Has repository url`` → ``has_repository_url`` (template parameter form)."""
    name = property_name[len("Has "):] if property_name.startswith("Has ") else property_name
    return "has_" + name.replace(" ", "_").lower()


def _build_property_index(schema: Schema) -> dict[str, str]:
    """Lower-cased property name → canonical name, for case-insensitive lookup."""
    return {name.lower(): name for name in schema.properties}


def validate_file(
    file: WikiYmlFile,
    schema: Schema,
    *,
    expected_kinds: Iterable[str] | None = None,
    ci_injected: Iterable[str] | None = None,
) -> list[ValidationIssue]:
    """Validate a single ``wiki.yml`` file. Returns a list of issues; empty means clean.

    ``ci_injected`` is the set of property names the writer always supplies on output —
    those are exempt from the required-property check. If ``None``, the default set
    for the file's ``kind`` (from :data:`CI_INJECTED_BY_KIND`) is used.
    """
    issues: list[ValidationIssue] = []
    rel = str(file.relative_path)

    kind = file.kind
    if not kind:
        issues.append(
            ValidationIssue(Severity.ERROR, rel, "missing required field: kind")
        )
        return issues

    if expected_kinds is not None and kind not in set(expected_kinds):
        allowed = ", ".join(sorted(set(expected_kinds)))
        issues.append(
            ValidationIssue(
                Severity.ERROR, rel, f"unknown kind {kind!r} (expected one of: {allowed})"
            )
        )
        return issues

    category_name = kind_to_category_name(kind)
    category = schema.categories.get(category_name)
    if category is None:
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                rel,
                f"kind {kind!r} maps to Category {category_name!r}, which is not "
                "installed on the destination wiki",
            )
        )
        return issues

    injected = set(ci_injected) if ci_injected is not None else set(ci_injected_for_kind(kind))
    issues.extend(_check_required_properties(file, category, rel, injected))
    issues.extend(_check_unknown_keys(file, schema, rel))
    return issues


def validate_files(
    files: list[WikiYmlFile], schema: Schema, *, expected_kinds: Iterable[str] | None = None
) -> list[ValidationIssue]:
    """Validate every file in ``files`` against ``schema``. Returns the combined issue list."""
    all_issues: list[ValidationIssue] = []
    for file in files:
        all_issues.extend(validate_file(file, schema, expected_kinds=expected_kinds))
    return all_issues


def has_errors(issues: Iterable[ValidationIssue]) -> bool:
    """True if any issue has severity ``error``."""
    return any(i.severity == Severity.ERROR for i in issues)


def _check_required_properties(
    file: WikiYmlFile, category: CategoryDef, rel: str, ci_injected: set[str]
) -> list[ValidationIssue]:
    """Every required Property field on the Category must have a corresponding wiki.yml key,
    unless the property is in ``ci_injected`` (the bridge fills it in automatically)."""
    issues: list[ValidationIssue] = []
    file_keys_as_props = {yaml_key_to_property_name(k).lower() for k in file.content}
    injected_lower = {p.lower() for p in ci_injected}
    for required in category.required_properties():
        if required.lower() in injected_lower:
            continue
        if required.lower() not in file_keys_as_props:
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    rel,
                    f"missing required property {required!r} for Category {category.name!r}",
                )
            )
    return issues


def _check_unknown_keys(
    file: WikiYmlFile, schema: Schema, rel: str
) -> list[ValidationIssue]:
    """Top-level keys that aren't structural and don't map to a known property are flagged."""
    issues: list[ValidationIssue] = []
    prop_index = _build_property_index(schema)
    for key in file.content:
        if key in STRUCTURAL_KEYS:
            continue
        prop_name = yaml_key_to_property_name(key)
        if prop_name.lower() not in prop_index:
            issues.append(
                ValidationIssue(
                    Severity.WARNING,
                    rel,
                    f"unknown key {key!r} — does not match any known property "
                    f"(expected something like {prop_name!r})",
                )
            )
    return issues
