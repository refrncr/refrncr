import logging
import os
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import URL, make_url

from refrncr import cli
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import Organisation, User


def parse_start(*arguments):
    return cli.build_parser().parse_args(["start", *arguments])


def test_help_lists_start(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert "start" in capsys.readouterr().out


def test_help_exposes_first_admin_command_hierarchy_without_password_option(capsys):
    with pytest.raises(SystemExit) as root_exit:
        cli.main(["--help"])
    root_help = capsys.readouterr().out
    assert root_exit.value.code == 0
    assert "user" in root_help

    with pytest.raises(SystemExit) as user_exit:
        cli.main(["user", "--help"])
    user_help = capsys.readouterr().out
    assert user_exit.value.code == 0
    assert "create-admin" in user_help

    with pytest.raises(SystemExit) as command_exit:
        cli.main(["user", "create-admin", "--help"])
    command_help = capsys.readouterr().out
    assert command_exit.value.code == 0
    assert "first administrator" in command_help
    assert "--email" in command_help
    assert "--display-name" in command_help
    assert "--password" not in command_help


def test_start_defaults_to_loopback_and_persistent_platform_data(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("REFRNCR_DATABASE_URL", raising=False)
    monkeypatch.delenv("REFRNCR_HOST", raising=False)
    monkeypatch.delenv("REFRNCR_PORT", raising=False)
    monkeypatch.delenv("REFRNCR_TRUSTED_PROXIES", raising=False)

    configuration = cli.resolve_start_configuration(parse_start())

    assert configuration.host == "127.0.0.1"
    assert configuration.port == 8000
    assert configuration.trusted_proxies == ()
    assert isinstance(configuration.database_url, URL)
    assert configuration.database_url.database.endswith("/refrncr/refrncr.db")
    assert configuration.database_url.database != ":memory:"


def _create_admin(database, email, display_name, passwords):
    answers = iter(passwords)
    return cli.main(
        [
            "user",
            "create-admin",
            "--email",
            email,
            "--display-name",
            display_name,
            "--database-path",
            str(database),
        ],
        password_reader=lambda _prompt: next(answers),
    )


def _stored_users(database):
    engine = create_database_engine(cli.sqlite_url(database))
    try:
        with create_session_factory(engine)() as session:
            return list(session.scalars(select(User)))
    finally:
        engine.dispose()


def test_create_admin_bootstraps_exactly_one_canonical_active_admin(tmp_path, capsys, caplog):
    database = tmp_path / "community.db"
    password = "  cafe\N{COMBINING ACUTE ACCENT} password  "
    caplog.set_level(logging.DEBUG)

    assert (
        _create_admin(
            database,
            " Admin@Example.COM ",
            " Administrator Name ",
            [password, password],
        )
        == 0
    )

    users = _stored_users(database)
    assert len(users) == 1
    admin = users[0]
    assert admin.email == "admin@example.com"
    assert admin.display_name == "Administrator Name"
    assert admin.role == "admin"
    assert admin.is_active and not admin.is_deleted
    assert PasswordHasher().verify(admin.password_hash, password)
    output = capsys.readouterr()
    assert "First Community administrator created" in output.out
    for secret in (password, admin.password_hash):
        assert secret not in output.out
        assert secret not in output.err
        assert secret not in caplog.text


@pytest.mark.parametrize(
    ("password", "expected_exit"),
    [
        ("a" * 12, 0),
        ("a" * 1024, 0),
        ("a" * 11, 1),
        ("é" * 513, 1),
    ],
)
def test_create_admin_reuses_password_policy_boundaries(tmp_path, password, expected_exit, capsys):
    database = tmp_path / f"boundary-{len(password)}-{len(password.encode('utf-8'))}.db"

    assert (
        _create_admin(
            database,
            "admin@example.com",
            "Administrator",
            [password, password],
        )
        == expected_exit
    )

    users = _stored_users(database)
    assert len(users) == (1 if expected_exit == 0 else 0)
    captured = capsys.readouterr()
    assert password not in captured.out
    assert password not in captured.err


def test_password_confirmation_mismatch_fails_without_persistence(tmp_path, capsys):
    database = tmp_path / "mismatch.db"

    assert (
        _create_admin(
            database,
            "admin@example.com",
            "Administrator",
            ["correct horse battery staple", "different secure password"],
        )
        == 1
    )

    assert _stored_users(database) == []
    error = capsys.readouterr().err
    assert "confirmation does not match" in error
    assert "correct horse battery staple" not in error
    assert "different secure password" not in error


def test_password_reader_exception_does_not_expose_credentials(tmp_path, capsys):
    database = tmp_path / "reader-failure.db"
    secret = "reader exception credential"

    def fail_reader(_prompt):
        raise RuntimeError(secret)

    assert (
        cli.main(
            [
                "user",
                "create-admin",
                "--email",
                "admin@example.com",
                "--display-name",
                "Administrator",
                "--database-path",
                str(database),
            ],
            password_reader=fail_reader,
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "password could not be read safely" in captured.err
    assert secret not in captured.out
    assert secret not in captured.err
    assert _stored_users(database) == []


@pytest.mark.parametrize(
    ("email", "display_name"),
    [("malformed email", "Administrator"), ("admin@example.com", "   ")],
)
def test_zero_user_invalid_identity_leaves_database_empty(tmp_path, email, display_name, capsys):
    database = tmp_path / f"invalid-{uuid.uuid4().hex}.db"
    password = "correct horse battery staple"

    assert _create_admin(database, email, display_name, [password, password]) == 1

    assert _stored_users(database) == []
    captured = capsys.readouterr()
    assert password not in captured.out
    assert password not in captured.err


def test_create_admin_requires_tty_and_ignores_password_environment(monkeypatch, tmp_path, capsys):
    class NonInteractiveInput:
        @staticmethod
        def isatty():
            return False

    database = tmp_path / "non-tty.db"
    monkeypatch.setattr(cli.sys, "stdin", NonInteractiveInput())
    monkeypatch.setenv("REFRNCR_PASSWORD", "environment credential must be ignored")

    assert (
        cli.main(
            [
                "user",
                "create-admin",
                "--email",
                "admin@example.com",
                "--display-name",
                "Administrator",
                "--database-path",
                str(database),
            ]
        )
        == 1
    )

    assert _stored_users(database) == []
    error = capsys.readouterr().err
    assert "interactive terminal is required" in error
    assert "environment credential" not in error


@pytest.mark.parametrize("warning_on_read", [1, 2])
def test_create_admin_fails_closed_when_getpass_cannot_disable_echo(
    monkeypatch, tmp_path, capsys, caplog, warning_on_read
):
    class InteractiveInput:
        @staticmethod
        def isatty():
            return True

    database = tmp_path / "unsafe-getpass.db"
    secret = "unsafe fallback credential"
    hash_secret = "$argon2id$unsafe-terminal-detail"
    fallback_accepted = False
    reads = 0
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(cli.sys, "stdin", InteractiveInput())

    def unsafe_getpass(_prompt):
        nonlocal fallback_accepted, reads
        reads += 1
        if reads == warning_on_read:
            warnings.warn(f"{secret} {hash_secret}", cli.getpass.GetPassWarning, stacklevel=2)
            fallback_accepted = True
        return secret

    monkeypatch.setattr(cli.getpass, "getpass", unsafe_getpass)

    assert (
        cli.main(
            [
                "user",
                "create-admin",
                "--email",
                "admin@example.com",
                "--display-name",
                "Administrator",
                "--database-path",
                str(database),
            ]
        )
        == 1
    )

    assert fallback_accepted is False
    assert _stored_users(database) == []
    captured = capsys.readouterr()
    assert "password could not be read safely" in captured.err
    for credential in (secret, hash_secret):
        assert credential not in captured.out
        assert credential not in captured.err
        assert credential not in caplog.text


def test_interrupted_create_admin_exits_nonzero_without_persisting_user(tmp_path, capsys):
    database = tmp_path / "interrupted.db"

    def interrupt(_prompt):
        raise KeyboardInterrupt

    assert (
        cli.main(
            [
                "user",
                "create-admin",
                "--email",
                "admin@example.com",
                "--display-name",
                "Administrator",
                "--database-path",
                str(database),
            ],
            password_reader=interrupt,
        )
        == 1
    )

    assert _stored_users(database) == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "refrncr failed: administrator creation interrupted\n"


@pytest.mark.parametrize(
    "password_arguments",
    [
        ["--password", "insecure command-line credential"],
        ["insecure positional credential"],
    ],
)
def test_create_admin_parser_rejects_password_arguments(capsys, password_arguments):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(
            [
                "user",
                "create-admin",
                "--email",
                "admin@example.com",
                "--display-name",
                "Administrator",
                *password_arguments,
            ]
        )

    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert "invalid command-line arguments" in error
    assert "insecure command-line credential" not in error
    assert "insecure positional credential" not in error


def test_initialized_outcome_precedes_all_input_and_password_handling(tmp_path, capsys):
    database = tmp_path / "initialized.db"
    password = "correct horse battery staple"
    assert (
        _create_admin(
            database,
            "admin@example.com",
            "Administrator",
            [password, password],
        )
        == 0
    )
    capsys.readouterr()

    def password_must_not_be_read(_prompt):
        pytest.fail("initialized installations must fail before password input")

    outcomes = []
    for email, display_name in [
        ("admin@example.com", "Administrator"),
        ("malformed email", ""),
        ("another@example.com", "Another Administrator"),
    ]:
        outcomes.append(
            cli.main(
                [
                    "user",
                    "create-admin",
                    "--email",
                    email,
                    "--display-name",
                    display_name,
                    "--database-path",
                    str(database),
                ],
                password_reader=password_must_not_be_read,
            )
        )

    assert outcomes == [1, 1, 1]
    assert len(_stored_users(database)) == 1
    errors = [line for line in capsys.readouterr().err.splitlines() if line]
    assert errors == ["refrncr failed: Community installation is already initialized"] * 3


def test_competing_cli_bootstrap_attempts_create_one_admin_and_fail_generically(
    monkeypatch, tmp_path
):
    database = tmp_path / "competing-bootstrap.db"
    configuration = cli.CreateAdminConfiguration(
        database_url=cli.sqlite_url(database),
        email="admin@example.com",
        display_name="Administrator",
    )
    cli.prepare_database(configuration.database_url)
    monkeypatch.setattr(cli, "prepare_database", lambda _database_url: None)
    readers_ready = Barrier(2)

    def attempt(index):
        reads = 0

        def synchronized_reader(_prompt):
            nonlocal reads
            reads += 1
            if reads == 1:
                readers_ready.wait()
            return "correct horse battery staple"

        try:
            cli.create_initial_admin(
                cli.CreateAdminConfiguration(
                    database_url=configuration.database_url,
                    email=f"admin{index}@example.com",
                    display_name=f"Administrator {index}",
                ),
                synchronized_reader,
            )
        except cli.CliError as exc:
            return str(exc)
        return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, range(2)))

    assert outcomes.count(None) == 1
    assert outcomes.count("Community installation is already initialized") == 1
    users = _stored_users(database)
    assert len(users) == 1
    assert users[0].role == "admin"
    assert users[0].is_active and not users[0].is_deleted


def test_create_admin_reuses_default_and_environment_database_configuration(monkeypatch, tmp_path):
    default_database = tmp_path / "default" / "refrncr.db"
    monkeypatch.setattr(cli, "default_database_path", lambda: default_database)
    monkeypatch.delenv("REFRNCR_DATABASE_URL", raising=False)
    answers = iter(["correct horse battery staple"] * 2)
    assert (
        cli.main(
            [
                "user",
                "create-admin",
                "--email",
                "default@example.com",
                "--display-name",
                "Default Administrator",
            ],
            password_reader=lambda _prompt: next(answers),
        )
        == 0
    )
    assert len(_stored_users(default_database)) == 1

    environment_database = tmp_path / "environment.db"
    monkeypatch.setenv("REFRNCR_DATABASE_URL", str(cli.sqlite_url(environment_database)))
    answers = iter(["correct horse battery staple"] * 2)
    assert (
        cli.main(
            [
                "user",
                "create-admin",
                "--email",
                "environment@example.com",
                "--display-name",
                "Environment Administrator",
            ],
            password_reader=lambda _prompt: next(answers),
        )
        == 0
    )
    assert len(_stored_users(environment_database)) == 1


def test_create_admin_reuses_explicit_database_url(tmp_path):
    database = tmp_path / "explicit-url.db"
    answers = iter(["correct horse battery staple"] * 2)

    assert (
        cli.main(
            [
                "user",
                "create-admin",
                "--email",
                "explicit@example.com",
                "--display-name",
                "Explicit Administrator",
                "--database-url",
                str(cli.sqlite_url(database)),
            ],
            password_reader=lambda _prompt: next(answers),
        )
        == 0
    )

    assert len(_stored_users(database)) == 1


def test_create_admin_postgresql_compatibility():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    administration_engine = create_engine(database_url)
    schema = f"cli_bootstrap_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    rendered_schema_url = schema_url.render_as_string(hide_password=False)
    answers = iter(["correct horse battery staple"] * 2)
    try:
        assert (
            cli.main(
                [
                    "user",
                    "create-admin",
                    "--email",
                    "admin@example.com",
                    "--display-name",
                    "Administrator",
                    "--database-url",
                    rendered_schema_url,
                ],
                password_reader=lambda _prompt: next(answers),
            )
            == 0
        )
        engine = create_engine(schema_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT role, is_active, is_deleted FROM users")
                ).one()
            assert row == ("admin", True, False)
        finally:
            engine.dispose()
    finally:
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration_engine.dispose()


def test_cli_options_override_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("REFRNCR_DATABASE_URL", "sqlite:///environment.db")
    monkeypatch.setenv("REFRNCR_HOST", "environment-host")
    monkeypatch.setenv("REFRNCR_PORT", "9000")
    database = tmp_path / "configured.db"

    configuration = cli.resolve_start_configuration(
        parse_start("--database-path", str(database), "--host", "localhost", "--port", "8765")
    )

    assert configuration.database_url == cli.sqlite_url(database)
    assert configuration.host == "localhost"
    assert configuration.port == 8765


def test_environment_configuration_is_used(monkeypatch):
    monkeypatch.setenv("REFRNCR_DATABASE_URL", "sqlite:///environment.db")
    monkeypatch.setenv("REFRNCR_HOST", "127.0.0.2")
    monkeypatch.setenv("REFRNCR_PORT", "8766")
    monkeypatch.setenv("REFRNCR_TRUSTED_PROXIES", "10.0.0.1, 192.0.2.0/24")

    configuration = cli.resolve_start_configuration(parse_start())

    assert configuration.database_url == "sqlite:///environment.db"
    assert configuration.host == "127.0.0.2"
    assert configuration.port == 8766
    assert configuration.trusted_proxies == ("10.0.0.1", "192.0.2.0/24")


def test_trusted_proxy_cli_options_override_environment(monkeypatch):
    monkeypatch.setenv("REFRNCR_TRUSTED_PROXIES", "10.0.0.1")
    configuration = cli.resolve_start_configuration(
        parse_start("--trusted-proxy", "192.0.2.1", "--trusted-proxy", "2001:db8::/32")
    )
    assert configuration.trusted_proxies == ("192.0.2.1", "2001:db8::/32")


def test_invalid_trusted_proxy_configuration_fails_safely(monkeypatch, capsys):
    monkeypatch.setenv("REFRNCR_TRUSTED_PROXIES", "not-an-address")
    assert cli.main(["start"]) == 1
    assert "trusted proxies must be IP addresses or CIDR networks" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["question?.db", "a space.db", "hash#.db", "percent%.db", "é.db"])
def test_sqlite_path_special_characters_create_the_exact_requested_file(tmp_path, name):
    database = tmp_path / "nested directory" / name

    cli.prepare_database(cli.sqlite_url(database))

    assert database.is_file()
    engine = create_database_engine(cli.sqlite_url(database))
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_empty_xdg_data_home_uses_absolute_linux_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "")

    directory = cli.default_data_directory()

    assert directory == tmp_path / ".local" / "share" / "refrncr"
    assert directory.is_absolute()


def test_empty_local_app_data_uses_absolute_windows_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", "")

    directory = cli.default_data_directory()

    assert directory == tmp_path / "AppData" / "Local" / "refrncr"
    assert directory.is_absolute()


def test_start_help_has_no_database_side_effect(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "prepare_database",
        lambda _url: pytest.fail("help must not prepare the database"),
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["start", "--help"])

    assert exit_info.value.code == 0
    assert "--database-path" in capsys.readouterr().out


@pytest.mark.parametrize("port", ["not-a-port", "0", "65536"])
def test_invalid_environment_port_fails_without_starting(monkeypatch, port, capsys):
    monkeypatch.setenv("REFRNCR_PORT", port)
    monkeypatch.setattr(cli, "start", lambda _configuration: pytest.fail("must not start"))

    assert cli.main(["start"]) == 1
    assert "port must" in capsys.readouterr().err


@pytest.mark.parametrize("level", ["debug", "INFO", "Warning", "error", "CRITICAL"])
def test_start_log_level_is_validated_and_normalized(level):
    args = cli.build_parser().parse_args(["start", "--log-level", level])
    assert args.log_level == level.lower()


def test_invalid_start_log_level_fails_before_start(monkeypatch, capsys):
    monkeypatch.setattr(cli, "start", lambda _configuration: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["start", "--log-level", "verbose-private-value"])
    assert exit_info.value.code == 2
    assert "verbose-private-value" not in capsys.readouterr().err


def test_first_run_migrates_sqlite_and_restart_is_idempotent(tmp_path):
    database = tmp_path / "nested" / "community.db"
    database_url = cli.sqlite_url(database)

    cli.prepare_database(database_url)
    assert database.is_file()
    engine = create_database_engine(database_url)
    assert "alembic_version" in inspect(engine).get_table_names()

    application = cli.build_application(database_url)
    with create_session_factory(engine)() as session:
        assert session.get(Organisation, "community") is not None

    cli.prepare_database(database_url)
    restarted = cli.build_application(database_url)

    assert TestClient(application).get("/api/health").json() == {"status": "ok"}
    response = TestClient(restarted, base_url="https://testserver").get("/")
    assert response.status_code == 200
    assert "Sign in to your account" in response.text
    assert "Overview" not in response.text
    engine.dispose()


def test_migration_failure_prevents_server_start(monkeypatch, capsys):
    server_started = False
    database_url = (
        "postgresql+psycopg://user:credential-secret@example.invalid/database?options=query-secret"
    )

    def fail_migration(configured_url):
        raise OSError(f"failure connecting to {configured_url}")

    def record_server_start(*_args, **_kwargs):
        nonlocal server_started
        server_started = True

    monkeypatch.setattr(cli, "prepare_database", fail_migration)
    monkeypatch.setattr(cli.uvicorn, "run", record_server_start)

    exit_code = cli.main(["start", "--database-url", database_url])

    assert exit_code == 1
    assert not server_started
    error = capsys.readouterr().err
    assert "database preparation failed" in error
    assert "check database availability and configuration, then retry" in error
    assert "credential-secret" not in error
    assert "query-secret" not in error
    assert database_url not in error


def test_start_passes_resolved_host_and_port_to_server(monkeypatch, capsys):
    received = {}
    monkeypatch.setattr(cli, "prepare_database", lambda _url: None)
    monkeypatch.setattr(
        cli,
        "configure_operational_logging",
        lambda **kwargs: received.update(logging_configured=True, logging_options=kwargs),
    )

    def build_application(_url, **kwargs):
        received.update(kwargs)
        return object()

    monkeypatch.setattr(cli, "build_application", build_application)

    def run(application, *, host, port, proxy_headers, access_log, log_config):
        received.update(
            application=application,
            host=host,
            port=port,
            proxy_headers=proxy_headers,
            access_log=access_log,
            log_config=log_config,
        )

    monkeypatch.setattr(cli.uvicorn, "run", run)

    exit_code = cli.main(
        ["start", "--database-url", "sqlite:///unused.db", "--host", "127.0.0.2", "--port", "8123"]
    )

    assert exit_code == 0
    assert received["host"] == "127.0.0.2"
    assert received["port"] == 8123
    assert received["allow_loopback_http"] is True
    assert received["trusted_proxies"] == ()
    assert received["proxy_headers"] is False
    assert received["access_log"] is False
    assert received["log_config"] is None
    assert received["logging_configured"] is True
    assert received["logging_options"] == {
        "log_level": None,
        "interactive": False,
        "host": "127.0.0.2",
        "port": 8123,
    }
    assert capsys.readouterr().err == ""


def test_start_with_trusted_proxy_disables_loopback_http_cookie_mode(monkeypatch):
    received = {}
    monkeypatch.setattr(cli, "prepare_database", lambda _url: None)
    monkeypatch.setattr(cli, "configure_operational_logging", lambda **_kwargs: None)

    def build_application(_url, **kwargs):
        received.update(kwargs)
        return object()

    monkeypatch.setattr(cli, "build_application", build_application)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *_args, **_kwargs: None)

    assert cli.main(["start", "--trusted-proxy", "127.0.0.1"]) == 0
    assert received["allow_loopback_http"] is False
    assert received["trusted_proxies"] == ("127.0.0.1",)


def test_server_bind_failure_propagates_nonzero_exit(monkeypatch):
    monkeypatch.setattr(cli, "prepare_database", lambda _url: None)
    monkeypatch.setattr(cli, "build_application", lambda _url, **_kwargs: object())
    monkeypatch.setattr(cli, "configure_operational_logging", lambda **_kwargs: None)

    def fail_to_bind(*_args, **_kwargs):
        raise SystemExit(1)

    monkeypatch.setattr(cli.uvicorn, "run", fail_to_bind)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["start", "--database-url", "sqlite:///unused.db"])

    assert exit_info.value.code == 1


def test_community_startup_has_no_cloud_or_tenant_dependency():
    source = (cli.Path(cli.__file__)).read_text()

    assert "refrncr_cloud" not in source
    assert "tenant" not in source
    assert "organisation_id" not in source
