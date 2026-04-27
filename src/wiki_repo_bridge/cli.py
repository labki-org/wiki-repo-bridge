import click


@click.group()
@click.version_option()
def main() -> None:
    """Sync repository metadata (wiki.yml) to MediaWiki + SemanticSchemas wikis."""


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--wiki",
    "wikis",
    multiple=True,
    required=True,
    help="MediaWiki API URL — repeat for each destination to validate against.",
)
def validate(repo_path: str, wikis: tuple[str, ...]) -> None:
    """Validate every wiki.yml under REPO_PATH against each wiki's installed schema."""
    raise NotImplementedError("schema fetcher + validator not yet implemented")


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--wiki",
    "wikis",
    multiple=True,
    required=True,
    help="MediaWiki API URL — repeat for each destination to write to.",
)
@click.option("--tag", required=True, help="Git tag triggering the sync (e.g. v1.2.0).")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the page tree that would be written without making any edits.",
)
def sync(repo_path: str, wikis: tuple[str, ...], tag: str, dry_run: bool) -> None:
    """Sync REPO_PATH at TAG to one or more wikis."""
    raise NotImplementedError("page writer not yet implemented")
