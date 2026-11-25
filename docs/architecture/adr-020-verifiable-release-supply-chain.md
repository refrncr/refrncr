# ADR-020: Verifiable Python release supply chain

Status: Accepted and operational.

## Context

REF-51 and REF-60 require reproducible release inputs, blocking security gates, one-time artifact
builds, an SBOM, and provenance without changing normal consumer dependency resolution or creating a
release during implementation.

## Decision

Release automation resolves the public runtime ranges through `pip-tools` into a committed,
hash-verified lock. Pull requests run read-only dependency, history-secret, workflow-integrity and
Python-SAST gates. Runtime vulnerability exceptions are exact, version-controlled and valid for at
most 30 days. A published GitHub Release is the only production trigger. Its workflow builds wheel
and sdist once, validates that artifact set, generates a CycloneDX SBOM, and passes the unchanged
files to one protected publication job for attestation, GitHub Release attachment and PyPI Trusted
Publishing. A public checksum manifest covers the wheel, sdist and locked-graph SBOM using their
flat release-asset names. An internal manifest additionally covers that public manifest and the
publication verifier; its own digest crosses the job boundary as a build-job output rather than
inside the artifact alone. The publish job fails closed unless that external digest, the internal
manifest and then the public asset manifest all verify in that order. Clean `pipx` startup and,
for stable versions, upgrade from the immediately preceding public version are blocking gates.
Retries accept existing GitHub/PyPI files only when their names and hashes match the validated
artifact set.

CodeQL pull-request analysis produces SARIF locally and blocks actionable results without write
authority. Main/scheduled analysis additionally uploads the same gated SARIF with the narrowly
scoped `security-events: write` permission.

## Consequences

Maintainers review a large generated lockfile and update it whenever runtime ranges change.
Consumer installs keep the compatible ranges from package metadata and are not claimed to resolve
identically. GitHub code scanning, artifact attestations, the `pypi-production` protected
environment and PyPI Trusted Publisher require administrator configuration outside the repository.
The external manifest digest detects replacement of an artifact together with its internal
manifest within this workflow run; it does not make the GitHub Actions transport independent of
GitHub's job-output and artifact controls.
Those controls were activated for the first public candidate; the runbook remains authoritative for
checking them before each release. Wheel, sdist and `refrncr.cdx.json` are artifact-attestation
subjects. The locked-graph SBOM is not claimed as a CycloneDX predicate generated independently
from each distribution artifact.

## Alternatives considered

Exact pins in package metadata were rejected because they would constrain consumers. Containers
and hosted scanners were rejected as outside REF-51. Giving pull-request jobs write permissions was
rejected in favor of a local SARIF gate plus upload only on trusted default-branch execution.
