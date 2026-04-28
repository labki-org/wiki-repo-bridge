import pytest

from wiki_repo_bridge.wikitext import (
    has_managed_block,
    render_bullet_list,
    render_section,
    render_subobject,
    render_template,
    replace_managed_block,
    wrap_managed,
)


class TestRenderTemplate:
    def test_simple(self) -> None:
        out = render_template("Project", {"has_description": "x", "has_name": "MiniXL"})
        assert out == "{{Project\n|has_description=x\n|has_name=MiniXL\n}}"

    def test_omits_none(self) -> None:
        out = render_template("Project", {"has_description": "x", "has_DOI": None})
        assert "has_DOI" not in out
        assert "has_description=x" in out

    def test_omits_empty_string(self) -> None:
        out = render_template("Project", {"has_description": "x", "has_DOI": ""})
        assert "has_DOI" not in out

    def test_list_value_comma_joined(self) -> None:
        out = render_template("Project", {"has_email": ["a@x.com", "b@y.com"]})
        assert "has_email=a@x.com, b@y.com" in out

    def test_bool_yes_no(self) -> None:
        out = render_template("X", {"flag_on": True, "flag_off": False})
        assert "flag_on=Yes" in out
        assert "flag_off=No" in out

    def test_newline_collapsed(self) -> None:
        out = render_template("X", {"has_description": "line1\nline2"})
        assert "has_description=line1 line2" in out

    def test_preserves_input_order(self) -> None:
        out = render_template("X", {"c": "1", "a": "2", "b": "3"})
        # Each param on its own line, in insertion order
        lines = [line for line in out.split("\n") if line.startswith("|")]
        assert lines == ["|c=1", "|a=2", "|b=3"]


class TestRenderSubobject:
    def test_subobject_template_name(self) -> None:
        out = render_subobject("BOM Item", {"has_item": "Resistor", "has_quantity": "10"})
        assert out.startswith("{{BOM Item/subobject")
        assert "|has_item=Resistor" in out
        assert "|has_quantity=10" in out


class TestRenderSection:
    def test_default_level(self) -> None:
        out = render_section("Specs", "* foo")
        assert out == "== Specs ==\n* foo"

    def test_level_3(self) -> None:
        out = render_section("Subsection", "body", level=3)
        assert out == "=== Subsection ===\nbody"


class TestRenderBulletList:
    def test_strings(self) -> None:
        out = render_bullet_list(["alpha", "beta"])
        assert out == "* alpha\n* beta"

    def test_empty(self) -> None:
        assert render_bullet_list([]) == ""


class TestManagedMarkers:
    def test_wrap_managed_brackets_body(self) -> None:
        out = wrap_managed("body")
        assert out.startswith("<!-- wiki-repo-bridge Start -->")
        assert out.endswith("<!-- wiki-repo-bridge End -->")
        assert "\nbody\n" in out

    def test_has_managed_block(self) -> None:
        assert has_managed_block(wrap_managed("x"))
        assert not has_managed_block("plain prose")
        assert not has_managed_block("<!-- wiki-repo-bridge Start --> only opener")

    def test_replace_managed_block_preserves_outside(self) -> None:
        existing = f"prefix\n\n{wrap_managed('old')}\n\nsuffix"
        out = replace_managed_block(existing, "new")
        assert out.startswith("prefix\n\n")
        assert out.endswith("\n\nsuffix")
        assert "old" not in out
        assert "new" in out

    def test_replace_managed_block_errors_when_markers_missing(self) -> None:
        with pytest.raises(ValueError):
            replace_managed_block("no markers here", "new")
