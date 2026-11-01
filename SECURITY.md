# Security

## Reporting a vulnerability

Use GitHub's private
[Report a vulnerability](https://github.com/refrncr/refrncr/security/advisories/new) form as
the primary reporting channel. If you cannot use that form, email
**security@refrncr.org** instead. Do not open a public issue, pull request or discussion for
a suspected vulnerability.

Include, where possible:

- the affected version or commit;
- the database engine (SQLite or PostgreSQL) and operating system;
- a description of the issue and its potential impact;
- non-destructive steps to reproduce, or a minimal safe proof of concept;
- a safe way to contact you with follow-up questions.

Do not include real credentials, API keys, personal or customer data, other secrets, or
exploit payloads. Use placeholder values and a minimal, non-destructive demonstration instead.

## What to expect

- We acknowledge your report within 5 working days.
- We investigate confidentially and keep you informed of meaningful progress.
- Any public acknowledgement is discussed with you first.

For critical and high-severity vulnerabilities, maintainers:

1. keep the report and investigation confidential;
2. assess the severity, impact and affected versions;
3. prepare a remediation or mitigation plan for the supported version;
4. keep the reporter informed while the plan is carried out; and
5. coordinate public disclosure only after a fix or mitigation is available.

Any GitHub Security Advisory contains only non-sensitive information needed to understand
the risk and update safely. This policy does not promise a CVE assignment or public
disclosure before a fix or mitigation is available.

## Supported versions

Only the latest released version of refrncr receives security fixes. A release candidate is
a released version for this policy when it is the latest published release.

## Scope

This policy covers the refrncr Community/core repository and its released packages. It does
not cover refrncr Cloud operations, hosted-service incidents, tenant data or Cloud-only
infrastructure and incident-response processes.

## Automated checks

Automated dependency, secret, workflow-integrity and Python-SAST checks support review but are
not a security certification. Runtime dependency findings block releases unless an exact,
approved, maximum-30-day exception is recorded. Operational details and triage rules are
documented in the [release supply-chain guide](docs/development/supply-chain.md).
