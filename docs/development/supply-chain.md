# Release dependency and supply-chain operations

## Runtime release lock

`requirements/release.in` mirrors the compatible runtime ranges in `pyproject.toml`.
`requirements/release.txt` is the generated Python 3.12 release-environment lock and includes
SHA-256 hashes for every resolved distribution. It controls CI/release preparation; it does not
promise identical transitive versions for a user's ordinary `pipx install refrncr`.

Regenerate and review it from the repository virtual environment:

```shell
python -m pip install -e ".[dev]"
python tools/compile_release_lock.py
python -m pip install --require-hashes -r requirements/release.txt
git diff -- requirements/release.in requirements/release.txt pyproject.toml
```

The script requires exactly pip-tools 7.6.1 and is the only canonical generator used locally and in
CI. It ignores ambient pip-tools configuration and explicitly fixes hash generation, resolver,
`--strip-extras`, build isolation, unsafe-package policy, no-upgrade behavior, hash reuse, LF
newlines, header/colour behavior, annotations and index/find-links emission. `--no-upgrade` keeps
the committed graph stable during verification; reviewed dependency updates deliberately change
the affected pins before regenerating with this same command.

Review direct and transitive version changes, their hashes, upstream release notes and audit result.
Dependabot proposes weekly pip and GitHub Actions updates; action updates must retain a full 40-byte
commit SHA and readable version comment.

## Security gates and triage

`security.yml` runs on pull requests, pushes to `main`, and daily. The locked runtime audit is
blocking. The development-environment audit remains visible but non-blocking. Gitleaks scans full
history with complete redaction and no report/comment upload; its synthetic fixture is assembled
only inside CI and must be detected. CodeQL uses `security-extended` Python queries. Pull requests
invoke `analyze` with `upload: never`, then gate local SARIF with read-only permissions. Trusted
main/scheduled jobs use the same local gate and only afterward publish SARIF from their separately
permissioned upload step.

Never suppress or broadly exclude a result to make CI green. Confirm whether it is runtime,
development-only, secret, or SAST exposure; update the dependency/code when possible; preserve
non-secret evidence in the PR. Do not paste credentials, customer data, exploit payloads or raw
secret findings into issues or the exception register.

The synthetic Gitleaks job checks only exit code 1 and absence of the complete fixture value in
captured output; it does not depend on presentation text, log level or locale. The actual
repository/history scan remains independently blocking. The sole Gitleaks allowlist entry combines
an exact `dataset_versions.py` path with the literal
Python argument form `key, allow_unpublished=True`. Gitleaks' generic-key rule otherwise mistakes
that historical code phrase for a credential. The allowlist cannot match another file or a token
value and has a configuration regression test.

## Runtime vulnerability exceptions

`security/vulnerability-exceptions.json` is empty by default. An entry is valid only when its
advisory, normalized package name and exact locked version match a scanner finding. Every entry
requires risk assessment, mitigation, accountable owner, named approver, creation/expiry dates and
a remediation plan. Expiry may be no later than 30 days after creation. Missing, malformed,
mismatched or expired entries fail the gate; deleting an entry restores the ordinary block.

## Controlled release preparation

The repository workflow listens only to a published GitHub Release, validates its tag/version and
that it targets current `main` with successful CI, then builds wheel and sdist once. It validates
those files, writes SHA-256 sums, generates `refrncr.cdx.json` from the locked graph, and transfers
one named artifact bundle. Only the protected `publish` job receives `contents: write`,
`id-token: write`, and `attestations: write`; it attests and publishes the exact downloaded wheel
and sdist and attaches the SBOM and checksums.

Before any real release, an administrator must:

1. enable GitHub code scanning/CodeQL availability and make the security checks required;
2. confirm artifact attestations are available for this public repository/organization;
3. create the `pypi-production` GitHub Environment and require Jeroen's approval;
4. create/verify the PyPI project and organization ownership, enable account security controls,
   and configure a Trusted Publisher restricted to this repository, workflow and environment;
5. write and approve release notes, then create the exact `v<pyproject version>` GitHub Release.

These settings are deliberately not changed by REF-51. No PyPI upload, GitHub Release, environment,
secret, ruleset or branch-protection setting is created during repository implementation.
