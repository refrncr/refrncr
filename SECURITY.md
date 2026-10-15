# Security

## Reporting a vulnerability

Please report suspected security vulnerabilities privately by email to
**security@refrncr.org**. Do not open a public issue, pull request or discussion for a
suspected vulnerability.

Include, where possible:

- the affected version or commit;
- the database engine (SQLite or PostgreSQL) and operating system;
- a description of the issue and its potential impact;
- steps to reproduce, or a minimal proof of concept.

Do not include real credentials, API keys, customer data or other secrets. Use placeholder
values instead.

## What to expect

- We acknowledge your report within 5 working days.
- We investigate, keep you informed of progress, and agree on a disclosure date with you.
- We credit reporters in the release notes, unless you prefer to remain anonymous.

## Supported versions

Refrncr has not yet had a stable release. Security fixes are made on the `main` branch.

## Automated checks

Automated dependency, secret, workflow-integrity and Python-SAST checks support review but are
not a security certification. Runtime dependency findings block releases unless an exact,
approved, maximum-30-day exception is recorded. Operational details and triage rules are
documented in the [release supply-chain guide](docs/development/supply-chain.md).
