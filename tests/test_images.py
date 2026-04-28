from pathlib import Path

from tests.conftest import write_text
from wiki_repo_bridge.images import (
    alias_filename,
    discover_images,
    file_sha1,
    render_image_thumb,
    wiki_filename,
)
from wiki_repo_bridge.walker import find_wiki_yml_files


def _seed(tmp_path: Path) -> None:
    write_text(tmp_path / "wiki.yml", "kind: project\nname: MiniXL\ndescription: x\n")


class TestDiscoverImages:
    def test_resolves_relative_paths(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        write_text(
            tmp_path / "baseplate" / "wiki.yml",
            "kind: hardware_component\nname: Baseplate\nversion: 0.1.0\n"
            "images:\n"
            "  - {path: assets/render.png, caption: Assembled, kind: render}\n",
        )
        (tmp_path / "baseplate" / "assets").mkdir(parents=True)
        (tmp_path / "baseplate" / "assets" / "render.png").write_bytes(b"fake png")
        files = find_wiki_yml_files(tmp_path)
        baseplate = next(f for f in files if "baseplate" in str(f.relative_path))
        decls, errors = discover_images(baseplate, repo_root=tmp_path)
        assert errors == []
        assert len(decls) == 1
        assert decls[0].caption == "Assembled"
        assert decls[0].kind == "render"
        assert decls[0].abs_path.name == "render.png"

    def test_missing_file_errors(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        write_text(
            tmp_path / "baseplate" / "wiki.yml",
            "kind: hardware_component\nname: Baseplate\nversion: 0.1.0\n"
            "images:\n  - {path: assets/missing.png}\n",
        )
        files = find_wiki_yml_files(tmp_path)
        baseplate = next(f for f in files if "baseplate" in str(f.relative_path))
        _, errors = discover_images(baseplate, repo_root=tmp_path)
        assert any("not found" in e for e in errors)

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        write_text(
            tmp_path / "baseplate" / "wiki.yml",
            "kind: hardware_component\nname: Baseplate\nversion: 0.1.0\n"
            "images:\n  - {path: ../escape.png}\n",
        )
        files = find_wiki_yml_files(tmp_path)
        baseplate = next(f for f in files if "baseplate" in str(f.relative_path))
        _, errors = discover_images(baseplate, repo_root=tmp_path)
        assert any("must stay inside" in e or "outside" in e for e in errors)

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        write_text(
            tmp_path / "baseplate" / "wiki.yml",
            "kind: hardware_component\nname: Baseplate\nversion: 0.1.0\n"
            "images:\n  - {path: /etc/passwd}\n",
        )
        files = find_wiki_yml_files(tmp_path)
        baseplate = next(f for f in files if "baseplate" in str(f.relative_path))
        _, errors = discover_images(baseplate, repo_root=tmp_path)
        assert errors  # any error is fine; we just need rejection

    def test_no_images_key_returns_empty(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        write_text(
            tmp_path / "h" / "wiki.yml",
            "kind: hardware_component\nname: H\nversion: 0.1.0\n",
        )
        files = find_wiki_yml_files(tmp_path)
        h = next(f for f in files if "h/" in str(f.path) or str(f.relative_path).startswith("h"))
        decls, errors = discover_images(h, repo_root=tmp_path)
        assert decls == []
        assert errors == []


class TestFilenames:
    def test_versioned_component(self) -> None:
        out = wiki_filename(
            project="MiniXL", component="Baseplate", version="1.0.2",
            stem="render", suffix="png",
        )
        assert out == "MiniXL_Baseplate_v1.0.2_render.png"

    def test_versioned_strips_v_prefix(self) -> None:
        out = wiki_filename(
            project="MiniXL", component="Baseplate", version="v1.0.2",
            stem="render", suffix="png",
        )
        assert out == "MiniXL_Baseplate_v1.0.2_render.png"

    def test_alias_component(self) -> None:
        out = alias_filename(
            project="MiniXL", component="Baseplate", stem="render", suffix="png",
        )
        assert out == "MiniXL_Baseplate_render.png"

    def test_project_image_omits_component(self) -> None:
        assert wiki_filename(
            project="MiniXL", component=None, version="1.0.0", stem="hero", suffix="jpg",
        ) == "MiniXL_v1.0.0_hero.jpg"
        assert alias_filename(
            project="MiniXL", component=None, stem="hero", suffix="jpg",
        ) == "MiniXL_hero.jpg"

    def test_slugify_collapses_special_chars(self) -> None:
        out = wiki_filename(
            project="MiniXL", component="Rigid-Flex PCB", version="0.1.0",
            stem="layout v2", suffix="png",
        )
        assert out == "MiniXL_Rigid_Flex_PCB_v0.1.0_layout_v2.png"


class TestRenderImageThumb:
    def test_with_caption(self) -> None:
        out = render_image_thumb("MiniXL_Baseplate_render.png", caption="Assembled")
        assert out == "[[File:MiniXL_Baseplate_render.png|thumb|right|300px|Assembled]]"

    def test_without_caption(self) -> None:
        out = render_image_thumb("MiniXL_Baseplate_render.png")
        assert out == "[[File:MiniXL_Baseplate_render.png|thumb|right|300px]]"


class TestFileSha1:
    def test_deterministic(self, tmp_path: Path) -> None:
        p = tmp_path / "f"
        p.write_bytes(b"abc")
        assert file_sha1(p) == "a9993e364706816aba3e25717850c26c9cd0d89d"

    def test_differs_for_different_content(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        a.write_bytes(b"hello")
        b = tmp_path / "b"
        b.write_bytes(b"world")
        assert file_sha1(a) != file_sha1(b)
