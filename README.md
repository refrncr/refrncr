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
