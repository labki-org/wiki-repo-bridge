from click.testing import CliRunner

from wiki_repo_bridge.cli import main


def test_help_lists_subcommands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "validate" in result.output
    assert "sync" in result.output


def test_validate_requires_wiki() -> None:
    with CliRunner().isolated_filesystem():
        import os
        os.makedirs("repo")
        result = CliRunner().invoke(main, ["validate", "repo"])
        assert result.exit_code != 0
        assert "--wiki" in result.output


def test_sync_requires_tag_and_wiki() -> None:
    with CliRunner().isolated_filesystem():
        import os
        os.makedirs("repo")
        result = CliRunner().invoke(main, ["sync", "repo"])
        assert result.exit_code != 0
