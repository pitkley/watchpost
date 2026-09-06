# Release quick reference

Run from the repository root with a clean tracked checkout. Requires `uv`, `git`,
`gh`, and Docker. Use `0.2.0rc1` / `0.2.0rc2` for RCs, or `0.2.0` for the final.

## 1. Prepare the version PR

```sh
RELEASE_VERSION=0.2.0rc1

git switch main
git pull --ff-only
git switch -c "release/$RELEASE_VERSION"
uv version "$RELEASE_VERSION"
uv lock --project examples/basic
uv run --no-project python .tools/check-release.py "v$RELEASE_VERSION"
git diff -- pyproject.toml uv.lock examples/basic/uv.lock
git add -- pyproject.toml uv.lock examples/basic/uv.lock
git commit -m "Prepare $RELEASE_VERSION release"
git push -u origin HEAD
gh pr create --base main --fill
gh pr checks --watch
```

Review and merge the PR once CI passes. CI runs the full validation suite.

## 2. Publish after merging

```sh
git switch main
git pull --ff-only
git show --stat --oneline HEAD
RELEASE_VERSION=$(uv version --short)
```

Confirm HEAD is the intended release commit. The next push publishes to
**TestPyPI, PyPI, GHCR, and GitHub**, including for RCs:

```sh
uv run --no-project python .tools/check-release.py "v$RELEASE_VERSION" &&
  git tag -a "v$RELEASE_VERSION" -m "Watchpost $RELEASE_VERSION" &&
  git push origin "refs/tags/v$RELEASE_VERSION"
gh run list --workflow release.yml --limit 5
```

Watch the matching Release run until it succeeds; approve deployments if prompted.

## 3. Verify

```sh
gh release view "v$RELEASE_VERSION"
uv run --isolated --no-project --with "watchpost[cli]==$RELEASE_VERSION" \
  python -c 'from importlib.metadata import version; print(version("watchpost"))'
docker buildx imagetools inspect \
  "ghcr.io/pitkley/watchpost/checkmk:$RELEASE_VERSION-checkmk-2.4.0p36"
```

Use the Checkmk version configured in `.github/workflows/docker.yml` if it changes.
`main` updates only `edge-checkmk-…`; legacy `latest` and Checkmk-only tags stay unchanged.

Release notes start empty. Generate and review them when needed, then attach the file:

```sh
gh release edit "v$RELEASE_VERSION" --notes-file docs/release-notes/0.2.0.md
```

**Retry:** use **Re-run failed jobs** on the same Release run. If a fix is needed,
prepare a new RC/patch version; never move a published tag.
