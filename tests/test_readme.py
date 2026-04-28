from pathlib import Path

import pytest

from wiki_repo_bridge.images import ImageUpload
from wiki_repo_bridge.readme import convert_readme, discover_readme

pytest.importorskip("pypandoc")


class TestDiscoverReadme:
    def test_finds_sibling_readme(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Title\n")
        assert discover_readme(tmp_path) == tmp_path / "README.md"

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert discover_readme(tmp_path) is None


class TestConvertReadme:
    def test_basic_markdown_to_wikitext(self, tmp_path: Path) -> None:
        md = tmp_path / "README.md"
        md.write_text("# Title\n\nSome **bold** text.\n")
        out = convert_readme(md)
        assert "= Title =" in out.wikitext
        assert "'''bold'''" in out.wikitext

    def test_strips_yaml_frontmatter(self, tmp_path: Path) -> None:
        md = tmp_path / "README.md"
        md.write_text("---\ntitle: Foo\nauthor: Bar\n---\n# Real heading\n")
        out = convert_readme(md)
        assert "title: Foo" not in out.wikitext
        assert "= Real heading =" in out.wikitext

    def test_strips_heading_anchor_spans(self, tmp_path: Path) -> None:
        md = tmp_path / "README.md"
        md.write_text("# Headline\n\n## Sub\n")
        out = convert_readme(md)
        assert "<span id=" not in out.wikitext

    def test_image_link_rewritten_to_alias_when_declared(self, tmp_path: Path) -> None:
        img_path = tmp_path / "render.png"
        img_path.write_bytes(b"fake")
        upload = ImageUpload(
            abs_path=img_path.resolve(),
            versioned_name="MiniXL_Baseplate_v0.1.0_render.png",
            alias_name="MiniXL_Baseplate_render.png",
            caption="Render",
        )
        md = tmp_path / "README.md"
        md.write_text("![Render](render.png)\n")
        out = convert_readme(md, images=[upload])
        assert "[[File:MiniXL_Baseplate_render.png" in out.wikitext

    def test_image_link_falls_back_to_tagged_url_when_undeclared(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "baseplate").mkdir(parents=True)
        md = repo / "baseplate" / "README.md"
        md.write_text("![Schematic](diagram.png)\n")
        out = convert_readme(
            md, repository_url="https://github.com/owner/repo",
            tag="v0.1.0", repo_root=repo,
        )
        assert "https://github.com/owner/repo/blob/v0.1.0/baseplate/diagram.png" in out.wikitext
        assert "[[File:" not in out.wikitext

    def test_relative_link_rewritten_to_tagged_blob_url(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        md = repo / "README.md"
        md.write_text("See [LICENSE](LICENSE) for details.\n")
        out = convert_readme(
            md, repository_url="https://github.com/owner/repo",
            tag="v0.1.0", repo_root=repo,
        )
        assert "[https://github.com/owner/repo/blob/v0.1.0/LICENSE LICENSE]" in out.wikitext

    def test_absolute_url_left_alone(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        md = repo / "README.md"
        md.write_text("[Anthropic](https://anthropic.com)\n")
        out = convert_readme(
            md, repository_url="https://github.com/owner/repo",
            tag="v0.1.0", repo_root=repo,
        )
        assert "https://anthropic.com" in out.wikitext
        assert "github.com/owner/repo/blob/v0.1.0" not in out.wikitext

    def test_component_readme_link_resolves_relative_to_repo_root(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "baseplate").mkdir(parents=True)
        md = repo / "baseplate" / "README.md"
        md.write_text("Print using [print.gcode](print.gcode).\n")
        out = convert_readme(
            md, repository_url="https://github.com/owner/repo",
            tag="v0.1.0", repo_root=repo,
        )
        # The link target was relative to baseplate/, but the URL must be repo-root-relative
        assert (
            "https://github.com/owner/repo/blob/v0.1.0/baseplate/print.gcode"
            in out.wikitext
        )
