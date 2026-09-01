from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from refrncr.app import create_application, create_community_services
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.services import ResourceTypeRegistry

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


@dataclass(frozen=True)
class StartConfiguration:
    database_url: str | URL
    host: str
    port: int


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
    parser = argparse.ArgumentParser(prog="refrncr", description="Run refrncr Community.")
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
    return parser


def resolve_start_configuration(args: argparse.Namespace) -> StartConfiguration:
    if args.database_path is not None:
        database_url = sqlite_url(args.database_path)
    elif args.database_url is not None:
        database_url = args.database_url
    else:
        database_url = os.environ.get("REFRNCR_DATABASE_URL") or sqlite_url(default_database_path())
    host = args.host or os.environ.get("REFRNCR_HOST") or DEFAULT_HOST
    raw_port = args.port if args.port is not None else os.environ.get("REFRNCR_PORT", DEFAULT_PORT)
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return StartConfiguration(database_url=database_url, host=host, port=port)


def prepare_database(database_url: str | URL) -> None:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}:
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def build_application(database_url: str | URL):
    engine = create_database_engine(database_url)
    services = create_community_services(
        create_session_factory(engine),
        ResourceTypeRegistry(),
    )
    return create_application(
        services.users,
        services.sessions,
        services.teams,
        services.memberships,
    )


def start(configuration: StartConfiguration) -> None:
    try:
        prepare_database(configuration.database_url)
        application = build_application(configuration.database_url)
    except Exception as exc:
        raise RuntimeError("database migration or bootstrap failed") from exc
    local_url = f"http://{configuration.host}:{configuration.port}"
    print(f"refrncr is starting at {local_url}", flush=True)
    uvicorn.run(application, host=configuration.host, port=configuration.port)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        configuration = resolve_start_configuration(args)
        start(configuration)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"refrncr failed to start: {exc}", file=sys.stderr)
        return 1
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
