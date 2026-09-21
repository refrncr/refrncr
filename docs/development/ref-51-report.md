# REF-51 implementation report

Date: 2026-09-21
Branch: `feat/ref-51-supply-chain-security`
Base: `e0fc7c9` (`origin/main` after fetch/fast-forward)

## Scope and impact

This Community/core change adds repository supply-chain controls only. It changes no application,
database, migration, API, authorization, tenant-isolation, audit, session, CSRF or browser
contract and adds no runtime package dependency. Architecture impact is recorded in ADR-020.

## Implemented controls and files

* `requirements/release.in`, `requirements/release.txt` and `tools/compile_release_lock.py`: public
  runtime ranges plus the canonically generated Python 3.12 lock.
* `tools/supply_chain.py` and `security/vulnerability-exceptions.json`: exact audit, exception,
  SARIF and action-pin policy gates.
* `.gitleaks.toml`: full redaction and one exact path+code false-positive exception.
* `.github/workflows/security.yml`, `codeql.yml`, `release.yml`: scheduled/PR/main security,
  CodeQL and controlled-release configuration; `ci.yml` is read-only and SHA-pinned.
* `tests/test_supply_chain.py`: 18 lock, metadata, finding, exception, pin, permission, SBOM and
  provenance workflow-contract tests.
* ADR-020, `docs/development/supply-chain.md`, README, CONTRIBUTING and SECURITY: architecture,
  maintainer process, triage, release boundary and manual prerequisites.

## Exact versions and action pins

Local tools: pip-tools 7.6.1, pip-audit 2.10.1, CycloneDX BOM 7.4.0, build 1.6.1, Twine 6.2.0,
Gitleaks 8.30.1 (upstream archive checksum verified).

| Action | Version comment | Immutable commit |
| --- | --- | --- |
| `actions/checkout` | v4.4.0 | `11d5960a326750d5838078e36cf38b85af677262` |
| `actions/setup-python` | v6.3.0 | `ece7cb06caefa5fff74198d8649806c4678c61a1` |
| `gitleaks/gitleaks-action` | v2.3.9 | `ff98106e4c7b2bc287b24eaf42907196329070c7` |
| `github/codeql-action` | v4.36.0 | `f52b05f4acaaa234e44466e66d29050e135ea9ef` |
| `actions/upload-artifact` | v4.6.2 | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| `actions/download-artifact` | v4.3.0 | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |
| `actions/attest` | v4.2.2 | `1e69f48acb82d1966a394da916b4c1698aa569d6` |
| `pypa/gh-action-pypi-publish` | v1.14.2 | `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` |

## Lock, scan and exception evidence

The lock resolves 31 packages and records SHA-256 distribution hashes. Pip-tools is pinned exactly
to 7.6.1. The sole local/CI generator ignores ambient configuration and fixes the resolver, hashes,
`--strip-extras`, build isolation, no-upgrade, unsafe-package behavior, hash reuse, LF newline,
header/colour behavior, annotations and emitted index/find-link options.
Two consecutive canonical generations produced byte-identical output (31 packages, 524 hashes;
SHA-256 `af54be00d3ef4699b0a2ed4dbd32a9c3b8a5f2605714dceef5a47b0e3ef34bed`).
A clean temporary environment installed it successfully with `pip --require-hashes`; the built
wheel then installed with `--no-deps` and `pip check` reported no broken requirements. Public
metadata still contains the original compatible ranges and the test enforces equality with the
lock input.

Pip-audit 2.10.1 reported `No known vulnerabilities found` for the locked runtime graph. There are
no retained findings and the exception register is empty. Tests cover a valid exception, missing
field, invalid/reversed date, expiry, over-30-day duration, advisory/package/version mismatch and
removal restoring the block. Development-only audit findings are deliberately visible/non-blocking.

Gitleaks 8.30.1 scanned 72 commits / approximately 7.36 MB. The initial redacted scan found one
false positive: generic-key interpreted the historical Python phrase `key,
allow_unpublished=True` in `dataset_versions.py` as a key. The reviewed allowlist requires both
that exact path and exact phrase. The rerun reported `no leaks found`. A temporary synthetic AWS-key
shape was detected with exit 1 while the captured output contained no fixture value. The CI
assertion depends only on those two facts, not on Gitleaks presentation wording.

CodeQL is configured for Python `security-extended`. PR jobs retain read-only permissions and pass
the required v4 enum `upload: never` to `analyze`; they gate the resulting local SARIF and fail on
warning/error results. Trusted main and daily runs use a separate job with `security-events: write`,
run the same local gate, then explicitly upload SARIF. GitHub
must still enable code scanning and make the checks required; this branch does not change settings.

## Permission, SBOM and provenance review

CI, security and PR-CodeQL default to `contents: read`. Main/scheduled CodeQL alone adds
`security-events: write`. Release build has `contents: read` plus `checks: read` to verify prior CI.
Only the protected `publish` job has `contents: write`, `id-token: write` and
`attestations: write`; no PR path can enter it.

A local CycloneDX JSON SBOM was generated and parsed successfully: `bomFormat=CycloneDX`, 31
components matching the 31 locked packages. The release workflow builds into
`release-artifacts/distributions` exactly once, validates those files, generates checksums/SBOM,
uploads one named artifact set, downloads that set, attests the exact `*.whl` and `*.tar.gz`, and
uses the same distribution directory for PyPI. No real attestation/release/upload was attempted.

## Verification results

| Check | Result |
| --- | --- |
| Focused REF-51 tests | 18 passed |
| Canonical lock regeneration | two runs byte-identical; CI invokes the same generator |
| Runtime `pip-audit` | 0 known vulnerabilities |
| Gitleaks repository/history | 72 commits, no findings after one exact documented false-positive rule |
| Gitleaks synthetic fixture | detected; value absent from captured output |
| CycloneDX generation/JSON parse | valid; 31 components |
| Hash-required clean install | passed |
| Wheel + sdist build / Twine | passed for both artifacts |
| Exact wheel install / `pip check` / CLI help | passed |
| Package and CLI targeted regression | 49 passed, 1 pre-existing platform skip, 1 warning |
| Ruff check / Ruff format check | passed |
| Pre-commit all files | all 7 hooks passed |
| Workflow YAML parse / action-pin policy / `git diff --check` | passed |
| Full suite with temporary PostgreSQL 16 and SQLite parametrizations | 3,870 passed, 0 skipped, 3 existing warnings; 800.14 s |

An additional sandboxed env-unset run reached completion with 2,700 passes, 1,167 expected
PostgreSQL skips and three failures caused solely by denied loopback socket access/default
PostgreSQL connectivity. It is not a product failure and is superseded by the successful full
PostgreSQL-enabled run outside that sandbox. No test skip, sleep or assertion was added or weakened.

## Capability limits and manual prerequisites

Repository files cannot prove that organization/repository code scanning, rulesets, attestations,
the `pypi-production` Environment or PyPI Trusted Publishing are enabled. Before a first release an
administrator must activate and protect those capabilities exactly as listed in the supply-chain
guide. GitHub's documented PR code-scanning check can become a required check through branch
protection; this implementation additionally has a repository-owned SARIF blocker so findings do
not depend solely on that external setting. CI remains the authoritative independent gate.

Remaining risks: external capability/permission availability is unproven until CI runs; the first
real release must verify GitHub attestation and PyPI OIDC configuration; scanner/advisory data is
time-dependent; Dependabot/action updates require ongoing review; no production release evidence
exists yet.

## Explicit staging list

```text
.github/workflows/ci.yml
.github/workflows/codeql.yml
.github/workflows/release.yml
.github/workflows/security.yml
.gitleaks.toml
CONTRIBUTING.md
README.md
SECURITY.md
docs/architecture/adr-020-verifiable-release-supply-chain.md
docs/development/ref-51-report.md
docs/development/supply-chain.md
pyproject.toml
requirements/release.in
requirements/release.txt
security/vulnerability-exceptions.json
tests/test_supply_chain.py
tools/supply_chain.py
```

Recommended commit: `feat(security): establish verified release supply chain (REF-51)`

Recommended push after review and commit:
`git push -u origin feat/ref-51-supply-chain-security`
