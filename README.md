# refrncr

Reference data without the enterprise baggage.

## Community user foundation

The Community application owns exactly one Organisation and supports multiple local users. On
application construction the Organisation is created when absent; `/setup` creates the first user
as its administrator. Administrators can then create, enable, disable, soft-delete, and change the
email address of users through the Flask web boundary. Passwords use Argon2id and authentication
uses database-backed server-side sessions.

SQLite supports the zero-infrastructure local experience. PostgreSQL is recommended for serious
concurrent multi-user operation.

## Community teams

Administrators can create, rename, list, and delete multiple teams within the single Community
installation. Team names preserve their NFC-normalized display form and are unique using Unicode
case-insensitive comparison. Administrators can add active users to teams and remove memberships;
users may belong to multiple teams, and teams may exist without members. Disabling or soft-deleting
a user retains their memberships, while deleting a team removes its memberships. Team permissions
are handled by later product stories.
