import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.engine import URL

from refrncr import cli
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import Organisation


def parse_start(*arguments):
    return cli.build_parser().parse_args(["start", *arguments])


def test_help_lists_start(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert "start" in capsys.readouterr().out


def test_start_defaults_to_loopback_and_persistent_platform_data(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("REFRNCR_DATABASE_URL", raising=False)
    monkeypatch.delenv("REFRNCR_HOST", raising=False)
    monkeypatch.delenv("REFRNCR_PORT", raising=False)

    configuration = cli.resolve_start_configuration(parse_start())

    assert configuration.host == "127.0.0.1"
    assert configuration.port == 8000
    assert isinstance(configuration.database_url, URL)
    assert configuration.database_url.database.endswith("/refrncr/refrncr.db")
    assert configuration.database_url.database != ":memory:"


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

    configuration = cli.resolve_start_configuration(parse_start())

    assert configuration.database_url == "sqlite:///environment.db"
    assert configuration.host == "127.0.0.2"
    assert configuration.port == 8766


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
    response = TestClient(restarted).get("/")
    assert response.status_code == 200
    assert "refrncr" in response.text
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
    assert "database migration or bootstrap failed" in error
    assert "credential-secret" not in error
    assert "query-secret" not in error
    assert database_url not in error


def test_start_passes_resolved_host_and_port_to_server(monkeypatch, capsys):
    received = {}
    monkeypatch.setattr(cli, "prepare_database", lambda _url: None)
    monkeypatch.setattr(cli, "build_application", lambda _url: object())

    def run(application, *, host, port):
        received.update(application=application, host=host, port=port)

    monkeypatch.setattr(cli.uvicorn, "run", run)

    exit_code = cli.main(
        ["start", "--database-url", "sqlite:///unused.db", "--host", "127.0.0.2", "--port", "8123"]
    )

    assert exit_code == 0
    assert received["host"] == "127.0.0.2"
    assert received["port"] == 8123
    assert "http://127.0.0.2:8123" in capsys.readouterr().out


def test_server_bind_failure_propagates_nonzero_exit(monkeypatch):
    monkeypatch.setattr(cli, "prepare_database", lambda _url: None)
    monkeypatch.setattr(cli, "build_application", lambda _url: object())

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
