# wiki-repo-bridge

Sync repository metadata to MediaWiki + [SemanticSchemas](https://github.com/labki-org/SemanticSchemas) wikis on tagged commits.

Part of the [labki](https://github.com/labki-org) ecosystem (alongside `labki-ontology`, `OntologySync`, `SemanticSchemas`, `ontology-hub`).

## What it does

Walks a repository for `wiki.yml` files (one at the repo root declaring the project, one per component subdirectory), validates them against the destination wikis' installed schema, and writes the resulting page tree to each wiki on each tagged release. Each tag produces:

- An immutable `Release` page bundling the specific component versions shipped at that tag.
- A canonical un-versioned page per component (`<Project>/Components/<Name>`), with `Has latest version` updated.
- An immutable versioned snapshot per component (`<Project>/Components/<Name>/<version>`).
- The `Project` page bootstrapped on first run; never overwritten thereafter (humans curate it, queries surface CI-managed data).

The wiki is the source of truth for the schema — Categories, Properties, and Subobjects are fetched from each destination wiki at run time and used to validate `wiki.yml` files. No schema duplication.

## Use as a GitHub Action

```yaml
on:
  push:
    tags: ['v*']

jobs:
  sync:
    uses: labki-org/wiki-repo-bridge/.github/workflows/sync.yml@main
    with:
      wikis: |
        https://wiki.example-lab.org/api.php
        https://example-public.org/wiki/api.php
    secrets: inherit  # passes WIKI_REPO_BOT_USER / WIKI_REPO_BOT_PASSWORD from org secrets
```

If your secrets live elsewhere or have different names, pass them explicitly instead of `inherit`:

```yaml
    secrets:
      WIKI_REPO_BOT_USER: ${{ secrets.MY_BOT_USER }}
      WIKI_REPO_BOT_PASSWORD: ${{ secrets.MY_BOT_PASSWORD }}
```

## CLI

```bash
# Validate every wiki.yml in a repo against destination wikis
wiki-repo-bridge validate ./my-repo --wiki https://wiki.example.org/api.php

# Sync a tagged release
wiki-repo-bridge sync ./my-repo --tag v1.2.0 \
  --wiki https://wiki.example-lab.org/api.php \
  --wiki https://example-public.org/wiki/api.php
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
