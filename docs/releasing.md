# Releasing

Releases are built and published by `.github/workflows/release.yml` when a version tag is pushed.

## One-time setup

1. On PyPI, add a **pending trusted publisher** for the project `agentic-chaos-security` (the PyPI name
   `agentic-chaos` belongs to an unrelated project): owner `RicoKomenda`, repository `agentic-chaos`, workflow
   `release.yml`, environment `pypi`. No API token is stored anywhere. The first release creates the project.
2. In the GitHub repository settings, create the environment `pypi` and require a reviewer for it.
3. For the docs site: Settings > Pages > Source "GitHub Actions", then add the repository variable `DOCS_DEPLOY=true`.
4. Optionally do the same for TestPyPI (environment `testpypi`) to rehearse a release.

## Each release

1. Update `version` in `pyproject.toml` (the package reads its version from its metadata).
2. Move the `Unreleased` entries in `CHANGELOG.md` under a heading for the new version.
3. Commit, then push a signed tag that matches the version: `git tag -s v1.0.0 -m v1.0.0 && git push origin v1.0.0`.
4. The workflow:
   - checks that the tag matches the package version,
   - runs the test suite,
   - builds the sdist and wheel,
   - creates signed build provenance (Sigstore, `actions/attest-build-provenance`),
   - publishes to PyPI with trusted publishing (PyPI adds its own digital attestations),
   - creates a GitHub release with the artifacts and the changelog section.

## Verifying a release

```bash
gh attestation verify agentic_chaos_security-1.0.0-py3-none-any.whl --repo RicoKomenda/agentic-chaos
```

The GitHub Action is versioned by the same tags. Move the major tag (`v1`) after each release so that
`uses: RicoKomenda/agentic-chaos@v1` picks it up.
