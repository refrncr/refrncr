# Release dependency and supply-chain operations

## Runtime release lock

`requirements/release.in` mirrors the compatible runtime ranges in `pyproject.toml`.
`requirements/release.txt` is the generated Python 3.12 release-environment lock and includes
SHA-256 hashes for every resolved distribution. It controls CI/release preparation; it does not
promise identical transitive versions for a user's ordinary `pipx install refrncr`.

The canonical lock is generated and checked only by the `dependency-and-policy` job on GitHub's
`ubuntu-24.04` runner with Python 3.12. The generator rejects other operating systems and Python
minor versions, so a macOS resolution cannot compete with the Linux release graph. To update the
lock, run the generator in that environment and review the resulting pull-request diff.

Developers verify—but do not regenerate—the committed lock locally:

```shell
python -m pip install -e ".[dev]"
python -m pip install --require-hashes -r requirements/release.txt
git diff -- requirements/release.in requirements/release.txt pyproject.toml
```

The Linux generator requires exactly pip-tools 7.6.1. It ignores ambient pip-tools configuration
and explicitly fixes hash generation, resolver,
`--strip-extras`, build isolation, unsafe-package policy, no-upgrade behavior, hash reuse, LF
newlines, header/colour behavior, annotations and index/find-links emission. `--no-upgrade` keeps
the committed graph stable during verification; reviewed dependency updates deliberately change
the affected pins before regenerating in the same canonical GitHub environment.

Review direct and transitive version changes, their hashes, upstream release notes and audit result.
Dependabot proposes weekly pip and GitHub Actions updates; action updates must retain a full 40-byte
commit SHA and readable version comment.

## Development-tool lock

`requirements/dev.in` and the generated, hash-pinned `requirements/dev.txt` keep the local and CI
development tools identical. CI installs the editable project and then installs this lock with
`--require-hashes`. Update the input deliberately, regenerate with
`.venv/bin/python tools/compile_dev_lock.py` in the canonical Linux Python 3.12 environment, and
review direct and transitive changes together with the release lock. The CI policy job regenerates
the file and fails if the committed lock is stale.

The test configuration ignores one known third-party warning from Python 3.12's SQLite datetime
adapter when SQLAlchemy binds datetime values. It is not emitted by Community code, and the same
database behavior is covered by both the SQLite and PostgreSQL suites; deprecations emitted by
`refrncr` itself remain errors.

## Security gates and triage

`security.yml` runs on pull requests, pushes to `main`, and daily. The locked runtime audit is
blocking. The development-environment audit remains visible but non-blocking. Gitleaks scans full
history with complete redaction. CI installs the open-source Gitleaks 8.30.1 CLI directly with Go
1.24.11 using a SHA-pinned `actions/setup-go`; it does not use the commercial action wrapper and
requires no `GITLEAKS_LICENSE` secret. Installation uses that release's declared historical Go
module ID, `github.com/zricethezav/gitleaks/v8`. The synthetic fixture is assembled only inside CI
and must be detected. CodeQL uses `security-extended` Python queries. Pull requests
invoke `analyze` with `upload: never`; both CodeQL jobs add metadata-only `actions: read` to
`contents: read` and gate local SARIF. Trusted main/scheduled jobs only afterward publish SARIF from
their separately permissioned upload step. Only that trusted job adds `security-events: write`.

Never suppress or broadly exclude a result to make CI green. Confirm whether it is runtime,
development-only, secret, or SAST exposure; update the dependency/code when possible; preserve
non-secret evidence in the PR. Do not paste credentials, customer data, exploit payloads or raw
secret findings into issues or the exception register.

The synthetic Gitleaks job checks only exit code 1 and absence of the complete fixture value in
captured output; it does not depend on presentation text, log level or locale. The actual
repository/history scan remains independently blocking. One Gitleaks allowlist entry combines an
exact `dataset_versions.py` path with the reviewed non-secret Python argument; a second covers the
same literal when quoted by the two REF-51 development documents in committed history. Gitleaks'
generic-key rule otherwise mistakes that phrase for a credential. Both entries require an exact
path and exact phrase, cannot match a token value, and have configuration regression tests.

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
