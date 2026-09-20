# refrncr

Reference data without the enterprise baggage.

## Quick start

Install and start the Community application with two commands:

```shell
pipx install refrncr
refrncr start
```

Open <http://127.0.0.1:8000>. The server binds only to the local loopback interface by
default. It uses a persistent embedded SQLite database, creates its parent directory, and applies
all Community database migrations before accepting requests. Docker and PostgreSQL are not
required for this quick start.

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

Existing installations must resolve ambiguous ownership before upgrading to REF-73. See
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

REF-83 adds immutable complete publications with UTC effective dates. Ordinary users read the
Current snapshot; active owning-team members and Community administrators manage the concept,
publish and cancel Scheduled versions. Historical Grants grant no editing or publication rights.
See [Dataset versions and upgrade preflight](docs/development/dataset-versions.md) before upgrading
an existing installation: every Dataset must have valid Fields, an identifier and valid values.
The controlled upgrade creates one System-published Version 1 per valid Dataset and fails closed
if any Dataset is invalid. Back up the database and stop writers before the upgrade.

## REF-40 API keys

API keys are available under Settings for active users when the installation
configures `REFRNCR_API_KEY_PEPPER`. Generate a 32-byte pepper as 64 hexadecimal
characters before starting the server:

```shell
export REFRNCR_API_KEY_PEPPER="$(openssl rand -hex 32)"
printf '%s' "$REFRNCR_API_KEY_PEPPER" | grep -Eq '^[0-9A-Fa-f]{64}$' && echo "pepper is valid"
refrncr start
```

For a source checkout, replace the final command with
`.venv/bin/refrncr start`. The variable must be set in the environment of the
process that starts refrncr. Exporting it in another terminal, or after the
server has started, does not update the running process. Stop and restart
refrncr after adding the variable.

Keep the generated value in protected installation configuration so every
restart receives the same pepper. Do not commit or log it. Changing or losing
it makes existing API keys unusable. See
[configuration, troubleshooting, rotation and authentication](docs/development/api-keys.md).
REF-40 supplies credentials for the Current published data REST API.

## Current REST API

The same application serves read-only Dataset metadata and Records under `/api/v1`.
Use an API key in the Authorization Bearer header; browser sessions do not authenticate
API requests. Collections use offset/limit pagination and private conditional reads
support ETag/304 revalidation. See the [REST contract and operations guide](docs/development/rest-api.md).
Public OpenAPI and Swagger delivery remains deferred to REF-109.


Public API documentation: open `/api/docs` for Swagger UI or `/api/openapi.json`
for the generated contract. Data endpoints require a Bearer API key and expose
Current published data only. See [REST API usage and documentation security](docs/development/rest-api.md).
