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


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    file: str  # relative path within the repo
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.file}: {self.message}"


def kind_to_category_name(kind: str) -> str:
    """Map a wiki.yml ``kind`` value to a wiki Category page name.

    ``hardware_component`` → ``Hardware Component``. The wiki stores Categories with
    Title-cased space-separated names; the YAML uses snake_case.
    """
    return " ".join(part.capitalize() for part in kind.split("_"))


def yaml_key_to_property_name(key: str) -> str:
    """Map a wiki.yml top-level key to its expected wiki Property name (case-preserving form)."""
    return "Has " + key.replace("_", " ")


def _build_property_index(schema: Schema) -> dict[str, str]:
    """Lower-cased property name → canonical name, for case-insensitive lookup."""
    return {name.lower(): name for name in schema.properties}


def validate_file(
    file: WikiYmlFile, schema: Schema, *, expected_kinds: Iterable[str] | None = None
) -> list[ValidationIssue]:
    """Validate a single ``wiki.yml`` file. Returns a list of issues; empty means clean."""
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

    issues.extend(_check_required_properties(file, category, rel))
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
    file: WikiYmlFile, category: CategoryDef, rel: str
) -> list[ValidationIssue]:
    """Every required Property field on the Category must have a corresponding wiki.yml key."""
    issues: list[ValidationIssue] = []
    file_keys_as_props = {yaml_key_to_property_name(k).lower() for k in file.content}
    for required in category.required_properties():
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
