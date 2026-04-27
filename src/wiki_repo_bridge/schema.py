"""Parsed schema dataclasses — the in-memory representation of a wiki's
SemanticSchemas-defined Categories and Properties."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PropertyField:
    """A property slot on a Category."""

    name: str  # e.g. "Has description"
    required: bool


@dataclass(frozen=True)
class SubobjectField:
    """A subobject slot on a Category — links to a target Category for the subobject's shape."""

    target_category: str  # e.g. "Project Role"
    required: bool


@dataclass
class CategoryDef:
    name: str
    description: str | None = None
    display_label: str | None = None
    parent_category: str | None = None
    show_backlinks_for: str | None = None
    target_namespace: str | None = None
    property_fields: list[PropertyField] = field(default_factory=list)
    subobject_fields: list[SubobjectField] = field(default_factory=list)

    def required_properties(self) -> set[str]:
        return {f.name for f in self.property_fields if f.required}

    def optional_properties(self) -> set[str]:
        return {f.name for f in self.property_fields if not f.required}


@dataclass
class PropertyDef:
    name: str
    description: str | None = None
    type: str | None = None  # "URL", "Page", "Text", "Number", "Date", etc.
    display_label: str | None = None
    allows_multiple_values: bool = False
    allows_value: list[str] = field(default_factory=list)  # enum values, if any
    allows_value_from_category: str | None = None  # for Page-typed properties


@dataclass
class Schema:
    """The full installed schema of a wiki — every Category and Property indexed by name."""

    categories: dict[str, CategoryDef] = field(default_factory=dict)
    properties: dict[str, PropertyDef] = field(default_factory=dict)
