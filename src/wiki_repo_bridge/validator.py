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


# Properties the bridge auto-injects on output for each kind, so the validator
# doesn't require them to be present in the wiki.yml. Component pages get
# their parent project filled in from the repo's top-level wiki.yml; family
# pages get latest_version computed; versioned pages get version + family +
# design_file_url synthesized by the writer.
CI_INJECTED_BY_KIND: dict[str, frozenset[str]] = {
    "project": frozenset(),
    "hardware_component": frozenset(
        {"Has project", "Has family", "Has latest version", "Has design file url"}
    ),
    "software_component": frozenset(
        {"Has project", "Has family", "Has latest version"}
    ),
    "firmware_component": frozenset(
        {"Has project", "Has family", "Has latest version"}
    ),
    "analysis_component": frozenset(
        {"Has project", "Has family", "Has latest version"}
    ),
}


def ci_injected_for_kind(kind: str | None) -> frozenset[str]:
    """Properties the bridge fills in automatically for a given wiki.yml kind.

    Used by the validator to skip required-property checks for properties the
    user shouldn't have to declare because the writer always provides them.
    """
    if kind is None:
        return frozenset()
    return CI_INJECTED_BY_KIND.get(kind, frozenset())


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

    ``hardware_component`` → ``Hardware component``. MediaWiki capitalizes only the
    first letter of a page title by default; the YAML uses snake_case and we
    convert to spaces preserving the rest of the lowercasing.
    """
    s = kind.replace("_", " ")
    return s[:1].upper() + s[1:] if s else s


def yaml_key_to_property_name(key: str) -> str:
    """Map a wiki.yml top-level key to its expected wiki Property name (case-preserving form)."""
    return "Has " + key.replace("_", " ")


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
