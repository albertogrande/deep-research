---
name: release
description: Cut a deepresearch release - version bump, CHANGELOG, tag, PyPI trusted publishing. Use when asked to release, publish to PyPI, or tag a version.
---

# Cutting a release

Releases publish to PyPI via trusted publishing (OIDC — no tokens in the repo). The tag push
is the trigger; `.github/workflows/release.yml` does the rest.

## Procedure

1. **Preflight** (all must be green):
   - `uv run pytest` — full offline suite.
   - `uv run ruff check . && uv run ruff format --check .`
   - `uv run pyright` (once Phase-11 typing lands).
2. **Version bump**: edit `version` in `pyproject.toml` (semver; pre-1.0 minor bumps for
   features, patch for fixes). Run `uv sync` so `uv.lock` records it.
3. **CHANGELOG.md**: move the `Unreleased` content under a new `## [X.Y.Z] - YYYY-MM-DD`
   heading (keep-a-changelog format). Leave an empty `Unreleased` section.
4. **JOURNAL.md**: releases are pushes — the journal mandate applies.
5. Commit: `release: vX.Y.Z`, then tag and push:
   ```bash
   git tag vX.Y.Z && git push && git push origin vX.Y.Z
   ```
6. The `release.yml` workflow builds with `uv build` and publishes via
   `pypa/gh-action-pypi-publish` (environment `pypi`, `id-token: write`).

## One-time PyPI setup (maintainer, manual)

On pypi.org → project → Publishing: add a trusted publisher for this GitHub repo,
workflow `release.yml`, environment `pypi`. Without this the publish step fails with an
OIDC error — nothing to fix in the repo.

## After the first release

- The README's `uvx deepresearch` quickstart becomes real — remove its "after first PyPI
  release" annotation.
- Consider MCP Registry publication for `deepresearch-mcp` (needs the published package).
