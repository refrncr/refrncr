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

The Linux generators require exactly the pip-tools version pinned in `requirements/dev.in`; they
read that source directly rather than duplicating the version in generator code. They ignore
ambient pip-tools configuration and explicitly fix hash generation, resolver,
`--strip-extras`, build isolation, unsafe-package policy, no-upgrade behavior, hash reuse, LF
newlines, header/colour behavior, annotations and index/find-links emission. `--no-upgrade` keeps
the committed graph stable during verification; reviewed dependency updates deliberately change
the affected pins before regenerating in the same canonical GitHub environment.

Review direct and transitive version changes, their hashes, upstream release notes and audit result.
Dependabot proposes weekly pip and GitHub Actions updates; action updates must retain a full 40-byte
commit SHA and readable version comment.

### Dependabot lock refresh

Python Dependabot pull requests are refreshed by `dependabot-lock-refresh.yml`. The workflow has
two deliberately separate jobs. The first job checks out the fixed pull-request head commit,
installs the pip-tools version declared by that commit, regenerates the locks and derived pins, and
uploads only a text patch. It has no token and cannot read the GitHub App secrets, so all of its
output is treated as untrusted.

The second job starts on a fresh runner at the same fixed head commit. Before downloading the
patch, it exports the validator and its two imports from the pull request's base commit into a
directory outside the pull-request worktree. It runs that trusted code from that directory with
`python -E -s`. The validator inspects the patch before `git apply`: only modifications to the two
lockfiles and the three managed derived-pin files are allowed; additions, deletions, renames, mode
changes, symlinks and binary hunks are rejected. After application it checks the allowlist again
and byte-compares every derived pin against output computed by the base-commit synchronizer. No
Python from the pull request is executed after the patch is applied.

Only after validation does the job check secret availability and mint a short-lived GitHub App
token. Configure the repository secrets `LOCK_REFRESH_CLIENT_ID` and
`LOCK_REFRESH_PRIVATE_KEY` for an App installed only on this repository with Contents and Workflows
read/write permission. The availability check receives booleans, not secret values; the private key
is passed only to the token action. Commits use the App bot identity, disable Git hooks, stage the
five allowlisted paths explicitly, and push without force. Per-PR concurrency cancels stale runs;
if the Dependabot branch changes before the push, the non-fast-forward push fails and the newer run
takes over.

Rotate the App private key by adding a new key in GitHub, replacing
`LOCK_REFRESH_PRIVATE_KEY`, verifying one refresh, and then deleting the old key. If generation or
validation fails, inspect the workflow annotation and reproduce the generators in canonical Linux
Python 3.12; never bypass the validator or manually grant a pull-request job a write token. The
workflow intentionally does not require `pip --only-binary`: dependency installation is confined
to the secretless untrusted job, while the trusted job installs no package from the pull request.

Fresh development-lock generation lets exact direct pins from `requirements/dev.in` move while it
keeps existing transitive versions as hard constraints. This minimizes unrelated lock churn, but a
direct update that requires a newer transitive dependency can therefore be unresolvable. In that
case the secretless generation job fails visibly and no patch, token or commit is produced. Update
the lock manually on the Dependabot branch after reviewing the required transitive change, or
update that transitive dependency first in a separate pull request, then rerun the refresh.

When an open Dependabot pull request predates this workflow or its base is too stale to refresh,
comment `@dependabot recreate` so Dependabot replaces its branch from current `main`. Use
`recreate`, not `rebase`, as the documented recovery command. During rollout, issue `recreate` on
every older open Dependabot pull request before relying on automatic lock refresh.

For the practical acceptance test, open or recreate a Python Dependabot pull request with stale
locks, record the head SHA, and verify that the two jobs run separately, the App-authored refresh
commit lands without force, all ordinary CI checks run on that commit, and a concurrent head change
causes the old push to fail or be cancelled. Also record Dependabot's actual response to an
`@dependabot rebase` comment, without relying on that command for recovery, then run
`@dependabot recreate` and verify that refresh succeeds on the replacement branch.

## Development-tool lock

`requirements/dev.in` is the source of truth for direct development-tool versions. The
`pyproject.toml` development extra, the hash-pinned `requirements/dev.txt`, the Ruff
pre-commit revision, the mypy pre-commit pin and shared release-tool pins must agree with it.

Run `python tools/sync_dependency_pins.py` after changing a managed input pin. The script updates
only the Ruff revision in `.pre-commit-config.yaml`, the pip-audit and pip-tools installations in
`.github/workflows/security.yml`, and the build, cyclonedx-bom, pip-audit, pipx and twine
installations in `.github/workflows/release.yml`. It uses targeted text replacement rather than
rewriting YAML, requires every expected source and target exactly once, validates every file before
writing any file, and is idempotent.

Use `python tools/sync_dependency_pins.py --check` to verify the derived pins without changing
files. The supply-chain workflow runs this check before regenerating either lock and reports the
same repair command when a pin is stale. It is deliberately not a pre-commit hook: CI remains the
repository-wide policy gate, while developers can run the explicit command when updating tools.

`requirements/dev.in` and the generated, hash-pinned `requirements/dev.txt` keep the local and CI
development tools identical. CI installs the editable project and then installs this lock with
`--require-hashes`. Update the input deliberately, regenerate with
`.venv/bin/python tools/compile_dev_lock.py` in the canonical Linux Python 3.12 environment, and
review direct and transitive changes together with the release lock. The CI policy job regenerates
the file from an empty output with `.venv/bin/python tools/compile_dev_lock.py --fresh` and fails if
the committed lock is stale or contains hashes that are not independently resolved.

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
those files, performs a clean `pipx` install and HTTP startup smoke test, generates
`refrncr.cdx.json` from the locked graph, and writes public SHA-256 sums for the wheel, sdist and
SBOM. The public manifest uses their bare GitHub release-asset names. An internal
`BUNDLE_SHA256SUMS` also covers the public manifest and `release_publication.py`; the build job
passes the internal manifest's digest separately as a job output. After download, the publish job
first checks that external anchor, then every internal bundle entry, and finally the public asset
manifest before using any bundle file. Only the protected `publish` job receives `contents: write`,
`id-token: write`, and `attestations: write`; it publishes only the exact wheel and sdist to PyPI,
attests wheel, sdist and SBOM as artifact subjects, and attaches those three assets plus the public
checksums to the GitHub Release. The locked-graph SBOM is not presented as an artifact-specific
CycloneDX predicate for either distribution.

To verify a current release, download `SHA256SUMS`, the wheel, the sdist and
`refrncr.cdx.json` into one directory and run:

```shell
sha256sum --check SHA256SUMS
gh attestation verify refrncr.cdx.json --repo refrncr/refrncr
gh attestation verify refrncr-<version>-py3-none-any.whl --repo refrncr/refrncr
gh attestation verify refrncr-<version>.tar.gz --repo refrncr/refrncr
```

For `0.1.0rc4` and earlier, the published manifest contains a `distributions/` prefix for wheel and
sdist. Create that directory and move those two downloaded files into it before running
`sha256sum --check SHA256SUMS`; earlier release assets and manifests are intentionally unchanged.

The build uses the tagged commit time as `SOURCE_DATE_EPOCH`. On a retry, existing GitHub assets
and PyPI distributions are accepted only when every filename and SHA-256 digest matches the newly
validated bundle. Missing GitHub assets are added; different or partial existing publications fail
closed. An identical PyPI publication is reported and skipped rather than uploaded again.

Pre-releases require clean-install evidence only. For every stable version, the workflow selects
the greatest published PEP 440 version below the candidate, installs it in a clean environment,
creates a representative administrator record, installs the candidate wheel, starts the upgraded
application to execute the complete migration chain, and verifies both the record and migration
state were preserved.

Before any real release, an administrator must:

1. enable GitHub code scanning/CodeQL availability and make the security checks required;
2. confirm artifact attestations are available for this public repository/organization;
3. create the `pypi-production` GitHub Environment and require Jeroen's approval;
4. create/verify the PyPI project and organization ownership, enable account security controls,
   and configure a Trusted Publisher restricted to this repository, workflow and environment;
5. write and approve release notes, then create the exact `v<pyproject version>` GitHub Release.

These settings are deliberately not changed by REF-51. No PyPI upload, GitHub Release, environment,
secret, ruleset or branch-protection setting is created during repository implementation.
