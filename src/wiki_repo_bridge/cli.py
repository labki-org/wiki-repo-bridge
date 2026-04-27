from __future__ import annotations

import os
import sys

import click

from wiki_repo_bridge.sync import (
    SyncError,
    categories_used_by_repo,
    execute_sync,
    plan_sync,
)
from wiki_repo_bridge.validator import has_errors
from wiki_repo_bridge.wiki_client import WikiClient


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
@click.option(
    "--bot-user",
    envvar="WIKI_BOT_USER",
    help="Bot username (env: WIKI_BOT_USER). Optional for read-only validation.",
)
@click.option(
    "--bot-password",
    envvar="WIKI_BOT_PASSWORD",
    help="Bot password (env: WIKI_BOT_PASSWORD). Optional for read-only validation.",
)
def validate(
    repo_path: str, wikis: tuple[str, ...], bot_user: str | None, bot_password: str | None
) -> None:
    """Validate every wiki.yml under REPO_PATH against each wiki's installed schema."""
    cats = categories_used_by_repo(repo_path)
    exit_code = 0
    for wiki_url in wikis:
        click.echo(f"=== {wiki_url} ===")
        client = WikiClient.from_api_url(wiki_url)
        if bot_user and bot_password:
            client.login(bot_user, bot_password)
        schema = client.load_schema(cats)
        # Use a placeholder tag for validation-only — tag isn't needed structurally.
        plan = plan_sync(repo_path, wiki_url, tag="v0.0.0", schema=schema)
        if not plan.issues:
            click.echo("ok — no issues")
            continue
        for issue in plan.issues:
            click.echo(str(issue))
        if has_errors(plan.issues):
            exit_code = 1
    sys.exit(exit_code)


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
    "--bot-user",
    envvar="WIKI_BOT_USER",
    required=True,
    help="Bot username (env: WIKI_BOT_USER).",
)
@click.option(
    "--bot-password",
    envvar="WIKI_BOT_PASSWORD",
    required=True,
    help="Bot password (env: WIKI_BOT_PASSWORD).",
)
@click.option(
    "--release-date",
    help="ISO date for the Release page. Defaults to today.",
)
@click.option(
    "--changelog",
    help="Release changelog text — typically the body of the GitHub release notes.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the page tree that would be written without making any edits.",
)
def sync(
    repo_path: str,
    wikis: tuple[str, ...],
    tag: str,
    bot_user: str,
    bot_password: str,
    release_date: str | None,
    changelog: str | None,
    dry_run: bool,
) -> None:
    """Sync REPO_PATH at TAG to one or more wikis."""
    cats = categories_used_by_repo(repo_path)
    overall_exit = 0
    for wiki_url in wikis:
        click.echo(f"=== {wiki_url} ===")
        client = WikiClient.from_api_url(wiki_url)
        client.login(bot_user, bot_password)
        schema = client.load_schema(cats)
        plan = plan_sync(
            repo_path,
            wiki_url,
            tag=tag,
            schema=schema,
            release_date=release_date,
            changelog=changelog,
        )
        if has_errors(plan.issues):
            for issue in plan.issues:
                click.echo(str(issue))
            click.echo(f"validation failed; skipping writes for {wiki_url}", err=True)
            overall_exit = 1
            continue
        try:
            results = execute_sync(plan, client, dry_run=dry_run)
        except SyncError as e:
            click.echo(str(e), err=True)
            overall_exit = 1
            continue
        for r in results:
            click.echo(str(r))
    if dry_run:
        click.echo("(dry-run — no writes performed)")
    if not os.environ.get("WIKI_BRIDGE_NO_EXIT"):
        sys.exit(overall_exit)
