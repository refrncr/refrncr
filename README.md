# refrncr

*Reference data without the enterprise baggage.*

Refrncr is a lightweight application for managing reference data that is accurate, shared and easy to consume. Create datasets with defined fields and identifiers, organise ownership through teams, and control who may edit, export or access data through the API. Work safely in Concepts: review changes, import versions, restore deleted records, and publish only when ready. Built-in validation, audit history, versioning and import flows protect data quality without adding enterprise complexity. Teams can browse published data in the web interface, download it as Excel, CSV, JSON or retrieve it through a REST API with personal API keys. Run it locally with your database and deployment setup.

Refrncr is source-available software licensed under the [Elastic License 2.0](LICENSE) (ELv2).
You may use, copy, modify and self-host Refrncr, including inside your organisation. You may not
offer Refrncr to third parties as a hosted or managed service that gives them access to a
substantial set of its features, circumvent any license key functionality, or remove licensing
and copyright notices. See [LICENSE](LICENSE) for the full terms.

Third-party dependencies are not bundled: pip installs them separately from their own
distributions, and they remain under their own licenses. This includes the PostgreSQL driver
`psycopg`, which is licensed under the LGPL-3.0. Licenses for assets shipped with Refrncr are
listed in `src/refrncr/static/licenses/`.

## Quick start

The PyPI package is not published yet. From a source checkout, create an isolated development
environment and install the Community application with:

```shell
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/refrncr start
```

Open <http://127.0.0.1:8000>. The server binds only to the local loopback interface by
default. It uses a persistent embedded SQLite database, creates its parent directory, and applies
all Community database migrations before accepting requests. Docker and PostgreSQL are not
required for this quick start.

Maintainers build releases from a committed, hash-verified runtime lock while the published package
metadata retains compatible dependency ranges for ordinary consumers. See the
[release supply-chain guide](docs/development/supply-chain.md); its configured release workflow is
not evidence that PyPI or GitHub release prerequisites have already been activated.

### Create the first administrator

On a new installation, open <http://127.0.0.1:8000/setup>. Enter the
administrator's email address and display name, and choose a password of at
least 12 characters. After creating the administrator, sign in at
<http://127.0.0.1:8000/login>. The setup page is available only while the
installation has no users; after the first administrator exists it redirects
to the login page.

The first administrator can instead be created from the command line before
starting the server:

```shell
refrncr user create-admin \
  --email admin@example.com \
  --display-name "Administrator"
```

The command prompts for the password and confirmation so the password does not
appear in command history. It uses the same default database as `refrncr start`.
When starting with `--database-path` or `--database-url`, pass the same option to
`user create-admin` so the administrator is created in the intended database.
Only the first administrator can be bootstrapped this way; create later users
from **Settings → Users** while signed in as an administrator.

The default database is stored in the operating system's user application-data directory:

- Linux: `$XDG_DATA_HOME/refrncr/refrncr.db`, or `~/.local/share/refrncr/refrncr.db` when
  `XDG_DATA_HOME` is unset;
- macOS: `~/Library/Application Support/refrncr/refrncr.db`;
- Windows: `%LOCALAPPDATA%\refrncr\refrncr.db`.

Configuration precedence is command-line option, then environment variable, then default:

```shell
refrncr start --database-path ./refrncr.db --host 127.0.0.1 --port 8000
refrncr start --database-url postgresql+psycopg://user:password@localhost/refrncr
```

The corresponding environment variables are `REFRNCR_DATABASE_URL`, `REFRNCR_HOST`, and
`REFRNCR_PORT`. `--database-path` is a convenient SQLite-only override; `--database-url` supports
any configured SQLAlchemy database URL. PostgreSQL remains an alternative persistence profile
handled by related stories rather than a quick-start requirement. Explicit trusted reverse proxies
can be configured with repeatable `--trusted-proxy IP_OR_CIDR` options or the comma-separated
`REFRNCR_TRUSTED_PROXIES` environment variable; direct Community use requires no proxy setting.
Configuring a trusted proxy disables plain-HTTP loopback cookies, so proxy deployments require
HTTPS.

## Development

Development uses Python 3.12 or newer. From the repository root:

```shell
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

For the exact CI-matching tool versions, install the editable project and the hash-pinned
development lock instead:

```shell
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install --require-hashes -r requirements/dev.txt
```

The default local test run uses SQLite and excludes tests marked `slow`:

```shell
.venv/bin/python -m pytest -m "not postgres"
```

Run the complete SQLite suite, including slow tests, with:

```shell
.venv/bin/python -m pytest --run-slow -m "not postgres"
```

The CI-equivalent parallel run uses all available workers:

```shell
.venv/bin/python -m pytest --run-slow -n auto --durations=50 -m "not postgres"
```

CI prints collection counts for each database and reports the slowest 50 cases. A call that
takes more than two seconds should be marked `slow`; CI reports unmarked cases above that
threshold as warnings without making timing alone fail the build.

The full database matrix also runs the tests marked `postgres`. PostgreSQL 16 is the supported
CI version. Start a local PostgreSQL 16 container, set `REFRNCR_TEST_POSTGRES_URL`, and run:

```shell
docker run --rm --name refrncr-postgres -e POSTGRES_DB=refrncr_test -e POSTGRES_USER=refrncr -e POSTGRES_PASSWORD=refrncr -p 5432:5432 postgres:16
export REFRNCR_TEST_POSTGRES_URL='postgresql+psycopg://refrncr:refrncr@127.0.0.1:5432/refrncr_test'
.venv/bin/python -m pytest
```

To run the suite with branch coverage locally, use the installed development extra:

```shell
.venv/bin/python -m pytest --cov
```

The configured coverage floor is 90%; migration files are excluded because Alembic owns their
execution. The optional quality tools can be installed and run with:

```shell
.venv/bin/python -m pip install -e ".[quality]"
.venv/bin/python -m radon cc src/refrncr -s -a
.venv/bin/python -m bandit -r src/refrncr
.venv/bin/python -m vulture src/refrncr
.venv/bin/python -m pip_audit
```

The repository checks are:

| Check | Command | Local use |
| --- | --- | --- |
| Tests | `.venv/bin/python -m pytest` | Required; PostgreSQL tests run when configured |
| Ruff lint | `.venv/bin/python -m ruff check .` | Required |
| Ruff format | `.venv/bin/python -m ruff format --check .` | Required |
| Pre-commit | `.venv/bin/python -m pre_commit run --all-files` | Required before review |
| Coverage | `.venv/bin/python -m pytest --cov` | Branch coverage; fails below 90% |
| Optional quality tools | `.venv/bin/python -m pip install -e ".[quality]"` | Radon, Bandit, Vulture and pip-audit; not a CI gate |
| CI-only security/release jobs | GitHub Actions | Run by CI |

Without `REFRNCR_TEST_POSTGRES_URL`, PostgreSQL-marked tests are skipped with an explicit reason;
SQLite tests still run. Use `pytest --durations=50` when profiling test feedback.

### Health and operational logs

After the existing automatic migration/bootstrap sequence succeeds, `GET /healthz`
reports process liveness and `GET /readyz` reports runtime database and Alembic-head
readiness. Probes are public, data-free and non-cacheable. `refrncr start` remains
fail-closed when its pre-start migration or bootstrap fails. Run migrations and
bootstrap successfully before relying on either probe.

At an interactive terminal the CLI prints a compact ready message with the browser URL;
successful request noise stays hidden. When stdout is redirected or collected, it emits
privacy-safe JSON lines instead. `--log-level` overrides `REFRNCR_LOG_LEVEL` for that
process; both accept `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. Interactive debug
uses only safe route categories, status, duration and request IDs and aggregates repeated
requests. Set `NO_COLOR` to disable terminal accents. Binding `--host 0.0.0.0` displays
`localhost` as the browser URL and states that every network interface is bound; no external
address is discovered. Every HTTP response receives an `X-Request-ID`. See the
[runtime operations guide](docs/development/operations.md) for probe usage, logging
fields and redaction boundaries.

Community operators are responsible for database backup scheduling, protected
storage, encryption, access and retention. See the
[operator backup and recovery guide](docs/development/backup-recovery.md) for verified
SQLite and PostgreSQL procedures; REFRNCR does not provide a backup service.

## Community user foundation

The Community application owns exactly one Organisation and supports multiple local users. On
application construction the Organisation is created when absent; `/setup` creates the first user
as its administrator. Administrators can then create, enable, disable, soft-delete, and change the
email address of users through the Flask web boundary. Passwords use Argon2id and authentication
uses database-backed server-side sessions.

The loopback and HTTPS cookie/session policy is documented in
[Browser session security](docs/development/browser-session-security.md).

SQLite supports the zero-infrastructure local experience. PostgreSQL is recommended for serious
concurrent multi-user operation.

The selected Argon2id parameters, per-operation resource implications, and opt-in benchmark are
documented in [Argon2id resource profile](docs/development/argon2-resource-profile.md).

## Community teams

Administrators can create, rename, list, and delete multiple teams within the single Community
installation. Team names preserve their NFC-normalized display form and are unique using Unicode
case-insensitive comparison. Administrators can add active users to teams and remove memberships;
users may belong to multiple teams, and teams may exist without members. Disabling or soft-deleting
a user retains their memberships. Ordinary active users can view their own teams. Teams represent
data domains: each Dataset has exactly one owning team, whose current members can maintain and
export it. Team deletion requires reassignment of every owned Dataset first.

Every active Community user can discover and read all Datasets and create a Dataset for one of
their teams. Administrators can create for any team and manage ownership and additional user/team
export access. Read access alone does not permit export. Historical Dataset viewer/editor/admin
Grants and creator metadata confer no Dataset rights.

Existing installations must resolve ambiguous ownership before upgrading. See
[Dataset governance and operator preflight](docs/development/dataset-governance.md) for the
authorization model, migration mapping, concurrency contract and backup/restore procedure.

## Audit coverage

Active Community administrators can browse **Settings → Audit log**. See
[audit history](docs/development/audit-history.md) for filters, safe detail presentation,
UTC/local time, cursor navigation and the read-only security boundary.

See the [audit policy](docs/development/audit-policy.md),
[functional inventory](docs/development/audit-inventory.json) and
[coverage matrix](docs/development/audit-coverage.json) for service-level audit contracts,
transactional mutation events, import summaries and prepared export request semantics.


### Published Dataset versions

Published Dataset versions are immutable complete snapshots with UTC effective dates. Ordinary users read the
Current snapshot; active owning-team members and Community administrators manage the concept,
publish and cancel Scheduled versions. Historical Grants grant no editing or publication rights.
See [Dataset versions and upgrade preflight](docs/development/dataset-versions.md) before upgrading
an existing installation: every Dataset must have valid Fields, an identifier and valid values.
The controlled upgrade creates one System-published Version 1 per valid Dataset and fails closed
if any Dataset is invalid. Back up the database and stop writers before the upgrade.

## API keys

API keys are available under Settings for active users when the installation
configures `REFRNCR_API_KEY_PEPPER`. For local development from a source checkout,
create the ignored `.env` file once:

```shell
umask 077
printf 'REFRNCR_API_KEY_PEPPER=%s\n' "$(openssl rand -hex 32)" > .env
.venv/bin/refrncr start
```

The CLI loads `.env` from its current working directory and does not override
variables already supplied by the launching environment. Stop and restart
refrncr after changing configuration.

Keep the generated value in protected installation configuration so every
restart receives the same pepper. Do not commit or log `.env`. Changing or losing
the pepper makes existing API keys unusable. See
[configuration, troubleshooting, rotation and authentication](docs/development/api-keys.md).
These API keys authenticate requests to the Current published data REST API.

## Current REST API

The same application serves read-only Dataset metadata and Records under `/api/v1`.
Use an API key in the Authorization Bearer header; browser sessions do not authenticate
API requests. Collections use offset/limit pagination and private conditional reads
support ETag/304 revalidation. See the [REST contract and operations guide](docs/development/rest-api.md).
Public API documentation: open `/api/docs` for Swagger UI or `/api/openapi.json`
for the generated contract. Data endpoints require a Bearer API key and expose
Current published data only. See [REST API usage and documentation security](docs/development/rest-api.md).
