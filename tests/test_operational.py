"""REF-70 health, readiness, correlation and structured-log contracts."""

import asyncio
import json
import logging
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from refrncr import cli
from refrncr.app import create_application
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent
from refrncr.operational import (
    LOGGER_NAME,
    InteractiveTerminalHandler,
    OperationalJSONFormatter,
    accepted_request_id,
    configure_operational_logging,
    resolve_log_level,
)
from refrncr.services import ResourceTypeRegistry


def terminal_record(level, event, **fields):
    record = logging.LogRecord(LOGGER_NAME, level, "", 0, "ignored", (), None)
    record.event = event
    for name, value in fields.items():
        setattr(record, name, value)
    return record


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "✓ refrncr is starting at http://127.0.0.1:8123\n  Press Ctrl+C to stop.\n"),
        (
            "0.0.0.0",
            "✓ refrncr is starting at http://localhost:8123\n"
            "  Listening on all network interfaces (0.0.0.0).\n"
            "  Press Ctrl+C to stop.\n",
        ),
    ],
)
def test_interactive_ready_presentation_is_safe_and_once(host, expected):
    import io

    stream = io.StringIO()
    handler = InteractiveTerminalHandler(stream, host=host, port=8123, color=False)
    handler.emit(terminal_record(logging.INFO, "application.starting"))
    handler.emit(terminal_record(logging.INFO, "application.started"))
    assert stream.getvalue() == expected


def test_interactive_debug_aggregates_equivalent_requests():
    import io

    stream = io.StringIO()
    handler = InteractiveTerminalHandler(stream, host="127.0.0.1", port=8000, color=False)
    fields = {
        "http_method": "GET",
        "route": "/static/{browser_path}",
        "status_code": 200,
        "duration_ms": 1.25,
        "request_id": "safe-id",
    }
    handler.emit(terminal_record(logging.DEBUG, "http.request_completed", **fields))
    handler.emit(terminal_record(logging.DEBUG, "http.request_completed", **fields))
    handler.emit(terminal_record(logging.DEBUG, "http.request_completed", **fields))
    handler.emit(terminal_record(logging.WARNING, "health.readiness_failed"))
    output = stream.getvalue()
    assert output.count("/static/{browser_path}") == 1
    assert "2 equivalent debug requests suppressed" in output
    assert "health.readiness_failed" in output


def test_interactive_colors_are_semantic_and_no_color_is_respected(monkeypatch, capsys):
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    try:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
        configure_operational_logging(interactive=True)
        logging.getLogger(LOGGER_NAME).info("ignored", extra={"event": "application.started"})
        assert "\033[32m" in capsys.readouterr().out

        monkeypatch.setenv("NO_COLOR", "")
        configure_operational_logging(interactive=True)
        logging.getLogger(LOGGER_NAME).info("ignored", extra={"event": "application.started"})
        assert "\033[" not in capsys.readouterr().out

        configure_operational_logging(interactive=True, log_level="warning")
        logging.getLogger(LOGGER_NAME).info("ignored", extra={"event": "application.started"})
        assert "refrncr is starting" in capsys.readouterr().out
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def operational_database(request, tmp_path):
    administration = None
    if request.param == "postgresql":
        database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
        administration = create_engine(database_url)
        schema = f"operational_{uuid.uuid4().hex}"
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        database_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema}"}
        )
    else:
        database_url = cli.sqlite_url(tmp_path / "operational.db")

    cli.prepare_database(database_url)
    engine = create_database_engine(database_url)
    services = cli.create_community_services(create_session_factory(engine), ResourceTypeRegistry())
    app = create_application(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
        dataset_records=services.dataset_records,
        dataset_versions=services.dataset_versions,
        audit_history=services.audit_history,
        api_keys=services.api_keys,
        readiness_engine=engine,
    )
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client, engine
    engine.dispose()
    if administration is not None:
        with administration.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()


def test_health_and_readiness_exact_public_contract_without_audit(operational_database):
    client, engine = operational_database
    with engine.connect() as connection:
        before = connection.scalar(select(func.count()).select_from(AuditEvent))

    for path in ("/healthz", "/readyz"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.content == b'{"status":"ok"}'
        assert response.headers["content-type"] == "application/json"
        assert response.headers["cache-control"] == "no-store"
        assert uuid.UUID(response.headers["x-request-id"]).version == 4
        assert "set-cookie" not in response.headers

    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(AuditEvent)) == before


def test_health_never_runs_readiness(monkeypatch, operational_database):
    client, _ = operational_database

    async def forbidden(_engine):
        raise AssertionError("health must not inspect the database")

    monkeypatch.setattr("refrncr.app.readiness_status", forbidden)
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "value",
    [None, "", "contains space", "x" * 129],
)
def test_missing_or_invalid_request_ids_are_replaced(value, operational_database):
    client, _ = operational_database
    headers = {} if value is None else {"X-Request-ID": value}
    response = client.get("/healthz", headers=headers)
    generated = response.headers["x-request-id"]
    assert uuid.UUID(generated).version == 4
    assert generated != value


def test_valid_request_id_is_returned_on_probe_browser_api_and_error(operational_database):
    client, _ = operational_database
    request_id = "deploy-check_123:node.4"
    for path in ("/healthz", "/", "/api/v1/datasets", "/missing-private-value"):
        response = client.get(path, headers={"X-Request-ID": request_id})
        assert response.headers["x-request-id"] == request_id


def test_duplicate_request_id_is_replaced():
    generated = accepted_request_id([(b"x-request-id", b"one"), (b"X-Request-ID", b"two")])
    assert uuid.UUID(generated).version == 4


def test_duplicate_request_id_is_replaced_over_http(operational_database):
    client, _ = operational_database
    response = client.get(
        "/healthz", headers=[("X-Request-ID", "first"), ("X-Request-ID", "second")]
    )
    assert uuid.UUID(response.headers["x-request-id"]).version == 4


@pytest.mark.parametrize("value", [b"line\nfeed", b"carriage\rreturn", b"non-ascii-\xff"])
def test_control_or_non_ascii_request_id_bytes_are_replaced(value):
    generated = accepted_request_id([(b"x-request-id", value)])
    assert uuid.UUID(generated).version == 4


@pytest.mark.parametrize("failure", [False, RuntimeError("private database secret")])
def test_readiness_failures_are_safe(monkeypatch, operational_database, failure):
    client, _ = operational_database

    async def unavailable(_engine):
        if failure:
            raise failure
        return False

    monkeypatch.setattr("refrncr.app.readiness_status", unavailable)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.content == b'{"status":"unavailable"}'
    assert response.headers["cache-control"] == "no-store"
    assert "private database secret" not in response.text


def test_missing_and_mismatched_migration_are_unavailable(operational_database):
    client, engine = operational_database
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='0001_ref_33'"))
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503


def test_readiness_timeout_is_bounded(monkeypatch):
    from refrncr import operational

    async def never_finishes(_function, *_args):
        await asyncio.Event().wait()

    monkeypatch.setattr(operational.asyncio, "to_thread", never_finishes)
    monkeypatch.setattr(operational, "READINESS_TIMEOUT_SECONDS", 0.01)
    assert asyncio.run(operational.readiness_status(object())) is False


def test_database_connection_exception_is_safe_readiness_failure():
    from refrncr import operational

    class UnavailableEngine:
        @staticmethod
        def connect():
            raise OSError("postgresql://user:secret@private-host/database")

    assert asyncio.run(operational.readiness_status(UnavailableEngine())) is False


def test_structured_request_logs_use_route_templates_and_safe_fields(caplog, operational_database):
    client, _ = operational_database
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    secret = "private-search-value"
    response = client.get(f"/api/v1/datasets/private-dataset?query={secret}")
    assert response.status_code == 401
    record = next(record for record in caplog.records if record.event == "http.request_completed")
    assert record.http_method == "GET"
    assert record.status_code == 401
    assert record.route.startswith("/api/v1")
    serialized = OperationalJSONFormatter().format(record)
    payload = json.loads(serialized)
    assert payload["severity"] == "INFO"
    assert payload["event"] == "http.request_completed"
    assert payload["request_id"] == response.headers["x-request-id"]
    assert isinstance(payload["duration_ms"], float)
    assert secret not in serialized
    assert "private-dataset" not in serialized
    assert "Authorization" not in serialized

    caplog.clear()
    browser_secret = "private-browser-query"
    browser_response = client.get(
        f"/datasets/private-dataset?search={browser_secret}", follow_redirects=False
    )
    browser_record = next(
        record for record in caplog.records if record.event == "http.request_completed"
    )
    browser_log = OperationalJSONFormatter().format(browser_record)
    assert browser_response.headers["x-request-id"] == browser_record.request_id
    assert browser_record.route == "/{browser_path}"
    assert "private-dataset" not in browser_log
    assert browser_secret not in browser_log


def test_unexpected_exception_returns_correlated_safe_error_and_log(caplog):
    from refrncr.operational import OperationalMiddleware

    app = FastAPI()
    app.add_middleware(OperationalMiddleware)

    @app.get("/failure/{private_value}")
    def fail(private_value: str):
        raise RuntimeError("credential-secret " + private_value)

    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    response = TestClient(app, raise_server_exceptions=False).get(
        "/failure/private-record?search=private-query"
    )
    assert response.status_code == 500
    assert response.json() == {"status": "unavailable"}
    record = next(record for record in caplog.records if record.event == "http.request_completed")
    assert record.levelname == "ERROR"
    assert record.error_category == "unexpected_exception"
    assert record.route == "/failure/{private_value}"
    assert record.request_id == response.headers["x-request-id"]
    serialized = OperationalJSONFormatter().format(record)
    for prohibited in ("credential-secret", "private-record", "private-query"):
        assert prohibited not in serialized


def test_failed_readiness_logs_warning_without_exception_details(caplog, operational_database):
    client, _ = operational_database
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    response = client.get("/readyz")
    assert response.status_code == 200

    with pytest.MonkeyPatch.context() as patch:

        async def unavailable(_engine):
            return False

        patch.setattr("refrncr.app.readiness_status", unavailable)
        response = client.get("/readyz")
    records = [
        record
        for record in caplog.records
        if getattr(record, "request_id", None) == response.headers["x-request-id"]
    ]
    assert {record.levelname for record in records} == {"WARNING"}
    assert {record.event for record in records} == {
        "health.readiness_failed",
        "http.request_completed",
    }


@pytest.mark.parametrize(
    ("configured", "expected", "invalid"),
    [
        (None, logging.INFO, False),
        ("warning", logging.WARNING, False),
        ("unsafe", logging.INFO, True),
    ],
)
def test_log_level_configuration(monkeypatch, configured, expected, invalid):
    monkeypatch.delenv("REFRNCR_LOG_LEVEL", raising=False)
    if configured is not None:
        monkeypatch.setenv("REFRNCR_LOG_LEVEL", configured)
    assert resolve_log_level() == (expected, invalid)


def test_configured_logger_emits_parseable_redacted_json(monkeypatch, capsys):
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    monkeypatch.setenv("REFRNCR_LOG_LEVEL", "invalid-private-setting")
    try:
        configure_operational_logging()
        logging.getLogger(LOGGER_NAME).error(
            "cookie authorization api-key search import email@example.com dataset record "
            "postgresql://user:secret@private-host/database",
            exc_info=RuntimeError("credential-secret"),
            extra={"event": "application.failure", "error_category": "unexpected_exception"},
        )
        lines = capsys.readouterr().out.splitlines()
        assert [json.loads(line)["event"] for line in lines] == [
            "logging.configuration_invalid",
            "application.failure",
        ]
        serialized = "".join(lines)
        for prohibited in (
            "cookie",
            "authorization",
            "api-key",
            "search",
            "import",
            "email@example.com",
            "dataset",
            "record",
            "postgresql://",
            "credential-secret",
        ):
            assert prohibited not in serialized.lower()
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)


def test_configured_log_level_filters_lower_severity(monkeypatch, capsys):
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    monkeypatch.setenv("REFRNCR_LOG_LEVEL", "WARNING")
    try:
        configure_operational_logging()
        logger = logging.getLogger(LOGGER_NAME)
        logger.info("not emitted", extra={"event": "test.info"})
        logger.warning("emitted", extra={"event": "test.warning"})
        lines = capsys.readouterr().out.splitlines()
        assert [json.loads(line)["event"] for line in lines] == ["test.warning"]
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)
