# Releasing

Releases use a stable SemVer tag, PyPI Trusted Publishing, and GitHub Actions.
The tag is the only publish trigger. Manual runs build and validate artifacts
without publishing them.

## One-time PyPI setup

The `blendersessiond` name is currently available on PyPI. Before pushing the
first release tag:

1. In the GitHub repository, create an environment named `pypi`. Add required
   reviewers if release approval should be manual.
2. In the PyPI account's **Publishing** page, add a pending GitHub publisher:
   - PyPI project name: `blendersessiond`
   - Owner: `BramVR`
   - Repository: `blendersessiond`
   - Workflow: `release.yml`
   - Environment: `pypi`

No API token or GitHub secret is needed. The first successful publish converts
the pending publisher into the project's normal Trusted Publisher.

## Prepare a release

1. Start from current `main` with CI and the required real-Blender smoke green.
2. Choose `X.Y.Z` and update it in:
   - `pyproject.toml`
   - `src/blendersessiond/__init__.py`
3. Run `uv lock`; this updates the editable package version in `uv.lock`.
4. Move the release entries from `[Unreleased]` to a dated section:
   `## [X.Y.Z] - YYYY-MM-DD`.
5. Run the full gate:

   ```console
   uv sync --locked
   uv run ruff check .
   uv run pytest
   uv build
   uvx --from twine twine check dist/*
   uv run python scripts/check_release.py vX.Y.Z
   ```

6. Commit and merge the release preparation.
7. From updated `main`, create and push the tag:

   ```console
   git tag -s vX.Y.Z -m "blendersessiond X.Y.Z"
   git push origin vX.Y.Z
   ```

The release workflow checks the tag against all three stored versions, checks
the dated changelog section, builds and smoke-tests the wheel, publishes the
wheel and source distribution to PyPI through OIDC, and creates a GitHub
Release using that changelog section. GitHub Release assets include both
distributions and `SHA256SUMS.txt`.

## Verify and close out

1. Confirm the workflow completed and the GitHub Release notes match the
   changelog.
2. Confirm PyPI shows `X.Y.Z`, the wheel, the source distribution, and the
   Trusted Publisher attestations.
3. Install from PyPI in a clean environment and check the entry point:

   ```console
   uvx --from blendersessiond==X.Y.Z blendersessiond --help
   ```

4. Add a new empty `[Unreleased]` section for the next patch cycle if release
   preparation removed it, then commit that closeout.
