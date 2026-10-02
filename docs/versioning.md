# Versioning and deprecation policy

Agentic Chaos follows [Semantic Versioning](https://semver.org/). The public surface is defined in
[api.md](api.md).

| Change | Release |
| --- | --- |
| Bug fix, new fault, probe, payload, injection point, CLI flag, report field, catalog experiment | patch or minor |
| Removing or renaming anything public; changing a default that changes verdicts; incompatible format change | major |

Before 1.0 (`0.x`), minor releases may still make breaking changes, but each one is listed under "Breaking" in
the [CHANGELOG](../CHANGELOG.md) and follows the deprecation process below whenever possible.

## Deprecation

1. The old behaviour keeps working and emits a `DeprecationWarning` (Python) or a warning on stderr (CLI) that
   names the replacement.
2. It stays for at least one minor release (at least three months after 1.0) before removal in a major release.
3. Deprecations are listed in the CHANGELOG when they are introduced and when they are removed.

## Experiment file format

- `apiVersion: agentic-chaos/v1` is stable. Within v1, changes are additive only: new optional fields, faults,
  probes and injection points. Files that validate today keep validating with the same meaning.
- `agentic-chaos/v1alpha1` (the pre-release format) is still accepted with a deprecation warning. Its
  structure is identical, so updating the `apiVersion` line is the whole migration.
- An incompatible format would be `v2`, shipped together with a migration command and supported side by side
  with `v1` for at least one major release.

## Verdict stability

Changes that can flip a verdict on an unchanged experiment are treated as breaking. Examples: default
thresholds, how pass rates are judged, how faults select calls, what a probe counts. Seeded runs make such
changes visible: the same file, seed and target give the same verdict across patch and minor releases.

## Interop

Third-party SDKs are not pinned (see [interop.md](interop.md)). When an SDK changes its wire behaviour,
Agentic Chaos adds support in a minor release and keeps supporting the previous behaviour while it is in
common use.
