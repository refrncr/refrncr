from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL, make_url

from refrncr.app import create_application, create_community_services
from refrncr.browser_security import is_loopback_host
from refrncr.dataset_upgrade import DatasetUpgradeError
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.login_security import SourceIdentityResolver
from refrncr.services import ConflictError, ResourceTypeRegistry, ValidationError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


@dataclass(frozen=True)
class StartConfiguration:
    database_url: str | URL
    host: str
    port: int
    trusted_proxies: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreateAdminConfiguration:
    database_url: str | URL
    email: str
    display_name: str


class CliError(Exception):
    """A controlled, secret-free command-line failure."""


PasswordReader = Callable[[str], str]


class SecretSafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: invalid command-line arguments\n")


class RefrncrArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed, unrecognized = self.parse_known_args(args, namespace)
        if unrecognized:
            if parsed.command == "user" and parsed.user_command == "create-admin":
                self.print_usage(sys.stderr)
                self.exit(2, f"{self.prog}: error: invalid command-line arguments\n")
            self.error(f"unrecognized arguments: {' '.join(unrecognized)}")
        return parsed


def default_data_directory() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "refrncr"


def default_database_path() -> Path:
    return default_data_directory() / "refrncr.db"


def sqlite_url(path: Path) -> URL:
    return URL.create("sqlite", database=str(path.expanduser().resolve()))


def build_parser() -> argparse.ArgumentParser:
    parser = RefrncrArgumentParser(prog="refrncr", description="Run refrncr Community.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start", help="Start the local Community web application.")
    database = start.add_mutually_exclusive_group()
    database.add_argument(
        "--database-url",
        help="SQLAlchemy database URL (overrides REFRNCR_DATABASE_URL).",
    )
    database.add_argument(
        "--database-path",
        type=Path,
        help="SQLite database path (overrides REFRNCR_DATABASE_URL).",
    )
    start.add_argument("--host", help="Bind host (default: REFRNCR_HOST or 127.0.0.1).")
    start.add_argument(
        "--port",
        type=int,
        help="Bind port (default: REFRNCR_PORT or 8000).",
    )
    start.add_argument(
        "--trusted-proxy",
        action="append",
        default=None,
        help=(
            "Trusted reverse-proxy IP or CIDR for X-Forwarded-For source identity "
            "(repeatable; default: REFRNCR_TRUSTED_PROXIES or none)."
        ),
    )

    user = subparsers.add_parser("user", help="Manage Community user bootstrap.")
    user_commands = user.add_subparsers(
        dest="user_command", required=True, parser_class=SecretSafeArgumentParser
    )
    create_admin = user_commands.add_parser(
        "create-admin",
        help="Bootstrap the first Community administrator.",
        description="Bootstrap the first administrator for a new refrncr Community installation.",
    )
    create_admin.add_argument("--email", required=True, help="Administrator email address.")
    create_admin.add_argument("--display-name", required=True, help="Administrator display name.")
    admin_database = create_admin.add_mutually_exclusive_group()
    admin_database.add_argument(
        "--database-url",
        help="SQLAlchemy database URL (overrides REFRNCR_DATABASE_URL).",
    )
    admin_database.add_argument(
        "--database-path",
        type=Path,
        help="SQLite database path (overrides REFRNCR_DATABASE_URL).",
    )
    preflight = subparsers.add_parser(
        "dataset-ownership-preflight",
        help="Inspect REF-73 ownership and optionally upgrade with an explicit mapping.",
    )
    database = preflight.add_mutually_exclusive_group()
    database.add_argument("--database-url")
    database.add_argument("--database-path", type=Path)
    preflight.add_argument(
        "--mapping", type=Path, help="JSON object: Dataset key to existing team ID."
    )
    preflight.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade after complete validation; stop all writers first.",
    )
    return parser


def resolve_database_url(args: argparse.Namespace) -> str | URL:
    if args.database_path is not None:
        return sqlite_url(args.database_path)
    if args.database_url is not None:
        return args.database_url
    return os.environ.get("REFRNCR_DATABASE_URL") or sqlite_url(default_database_path())


def resolve_start_configuration(args: argparse.Namespace) -> StartConfiguration:
    database_url = resolve_database_url(args)
    host = args.host or os.environ.get("REFRNCR_HOST") or DEFAULT_HOST
    raw_port = args.port if args.port is not None else os.environ.get("REFRNCR_PORT", DEFAULT_PORT)
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    trusted_proxies = args.trusted_proxy
    if trusted_proxies is None:
        configured = os.environ.get("REFRNCR_TRUSTED_PROXIES", "")
        trusted_proxies = [value.strip() for value in configured.split(",") if value.strip()]
    try:
        SourceIdentityResolver.from_config(trusted_proxies)
    except ValueError:
        raise ValueError("trusted proxies must be IP addresses or CIDR networks") from None
    return StartConfiguration(
        database_url=database_url,
        host=host,
        port=port,
        trusted_proxies=tuple(trusted_proxies),
    )


def resolve_create_admin_configuration(args: argparse.Namespace) -> CreateAdminConfiguration:
    return CreateAdminConfiguration(
        database_url=resolve_database_url(args),
        email=args.email,
        display_name=args.display_name,
    )


def prepare_database(database_url: str | URL, *, dataset_owner_mapping=None) -> None:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}:
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
            config.attributes["connection"] = connection
            config.attributes["dataset_owner_mapping"] = dataset_owner_mapping or {}
            command.upgrade(config, "head")
            _validate_ref_121_schema(connection)
    finally:
        engine.dispose()


def _validate_ref_121_schema(connection) -> None:
    revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if revision not in {"0010_ref_121", "0011_ref_130", "0012_ref_73", "0013_ref_39"}:
        return
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("dataset_fields")}
    position = columns.get("display_position")
    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("dataset_fields")
    }
    check_constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("dataset_fields")
    }
    if (
        position is None
        or position["nullable"]
        or "uq_dataset_fields_dataset_position" not in unique_constraints
        or "ck_dataset_fields_display_position_positive" not in check_constraints
    ):
        raise RuntimeError("REF-121 schema is incomplete; restore or repair the database")
    invalid = connection.scalar(
        text(
            "SELECT count(*) FROM dataset_fields AS field WHERE display_position < 1 OR "
            "display_position IS NULL OR display_position <> (SELECT count(*) FROM "
            "dataset_fields AS preceding WHERE preceding.dataset_id = field.dataset_id AND "
            "preceding.display_position <= field.display_position)"
        )
    )
    if invalid:
        raise RuntimeError("REF-121 field positions are not a dense positive order")


def build_application(
    database_url: str | URL,
    *,
    allow_loopback_http: bool = False,
    trusted_proxies: tuple[str, ...] = (),
):
    engine = create_database_engine(database_url)
    services = create_community_services(
        create_session_factory(engine),
        ResourceTypeRegistry(),
    )
    return create_application(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        audit_history=services.audit_history,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
        allow_loopback_http=allow_loopback_http,
        trusted_proxies=trusted_proxies,
    )


def start(configuration: StartConfiguration) -> None:
    try:
        prepare_database(configuration.database_url)
        application = build_application(
            configuration.database_url,
            allow_loopback_http=(
                is_loopback_host(configuration.host) and not configuration.trusted_proxies
            ),
            trusted_proxies=configuration.trusted_proxies,
        )
    except DatasetUpgradeError:
        raise
    except Exception as exc:
        raise RuntimeError("database migration or bootstrap failed") from exc
    local_url = f"http://{configuration.host}:{configuration.port}"
    print(f"refrncr is starting at {local_url}", flush=True)
    uvicorn.run(
        application,
        host=configuration.host,
        port=configuration.port,
        proxy_headers=False,
    )


def read_password_from_tty(prompt: str) -> str:
    if not sys.stdin.isatty():
        raise CliError("an interactive terminal is required to enter the administrator password")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            raise CliError("administrator password could not be read safely") from None


def read_confirmed_password(password_reader: PasswordReader) -> str:
    try:
        password = password_reader("Administrator password: ")
        confirmation = password_reader("Confirm administrator password: ")
    except (CliError, KeyboardInterrupt):
        raise
    except Exception as exc:
        raise CliError("administrator password could not be read safely") from exc
    if password != confirmation:
        raise CliError("administrator password confirmation does not match")
    return password


def create_initial_admin(
    configuration: CreateAdminConfiguration,
    password_reader: PasswordReader = read_password_from_tty,
) -> None:
    try:
        prepare_database(configuration.database_url)
        engine = create_database_engine(configuration.database_url)
    except Exception as exc:
        raise CliError("database migration or bootstrap failed") from exc

    try:
        try:
            services = create_community_services(
                create_session_factory(engine),
                ResourceTypeRegistry(),
            )
            services.users.bootstrap_organisation()
        except Exception as exc:
            raise CliError("database migration or bootstrap failed") from exc
        if services.users.has_users():
            raise CliError("Community installation is already initialized")

        password = read_confirmed_password(password_reader)
        try:
            services.users.create_initial_admin(
                configuration.email,
                configuration.display_name,
                password,
            )
        except ConflictError as exc:
            raise CliError("Community installation is already initialized") from exc
        except ValidationError as exc:
            raise CliError(str(exc)) from None
        except Exception as exc:
            raise CliError("administrator could not be created safely") from exc
    finally:
        engine.dispose()


def main(
    argv: Sequence[str] | None = None,
    *,
    password_reader: PasswordReader = read_password_from_tty,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "dataset-ownership-preflight":
            from refrncr.dataset_upgrade import ownership_preflight, read_owner_mapping

            database_url = resolve_database_url(args)
            mapping = read_owner_mapping(args.mapping)
            engine = create_database_engine(database_url)
            try:
                with engine.connect() as connection:
                    resolved, unresolved = ownership_preflight(connection, mapping)
                    teams = (
                        [
                            dict(row)
                            for row in connection.execute(
                                text("SELECT id, name FROM teams ORDER BY name")
                            ).mappings()
                        ]
                        if inspect(connection).has_table("teams")
                        else []
                    )
                print(
                    json.dumps(
                        {"resolved": resolved, "unresolved": unresolved, "teams": teams}, indent=2
                    )
                )
                if unresolved:
                    return 1
                if args.upgrade:
                    prepare_database(database_url, dataset_owner_mapping=mapping)
            finally:
                engine.dispose()
        elif args.command == "start":
            start(resolve_start_configuration(args))
        else:
            create_initial_admin(
                resolve_create_admin_configuration(args),
                password_reader,
            )
            print("First Community administrator created.")
    except KeyboardInterrupt:
        if args.command == "start":
            return 0
        print("refrncr failed: administrator creation interrupted", file=sys.stderr)
        return 1
    except CliError as exc:
        print(f"refrncr failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if args.command == "dataset-ownership-preflight":
            message = (
                str(exc)
                if isinstance(exc, DatasetUpgradeError)
                else "Database validation or upgrade failed. Check database availability and "
                "configuration; keep writers stopped and rerun the ownership preflight."
            )
            print(f"refrncr failed to check Dataset ownership: {message}", file=sys.stderr)
            return 1
        action = {
            "start": "start",
            "dataset-ownership-preflight": "check Dataset ownership",
        }.get(args.command, "create administrator")
        print(f"refrncr failed to {action}: {exc}", file=sys.stderr)
        return 1
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
