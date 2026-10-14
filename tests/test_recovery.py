"""REF-53 operator backup and recovery evidence for supported databases."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url

from refrncr import cli
from refrncr.app import create_community_services
from refrncr.cli import prepare_database
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, DatasetConcept
from refrncr.services import NotFoundError, ResourceTypeRegistry


@dataclass(frozen=True)
class RecoveryFixture:
    admin_id: str
    member_id: str
    outsider_id: str
    dataset_id: str
    dataset_key: str
    audit_count: int


def _services(database_url):
    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    return engine, sessions, create_community_services(sessions, ResourceTypeRegistry())


def _seed_representative_data(database_url) -> RecoveryFixture:
    prepare_database(database_url)
    engine, sessions, services = _services(database_url)
    try:
        admin = services.users.create_initial_admin(
            "recovery-admin@example.invalid", "Recovery admin", "synthetic password"
        )
        member = services.users.create_user(
            admin.id, "recovery-member@example.invalid", "Recovery member", "synthetic password"
        )
        outsider = services.users.create_user(
            admin.id,
            "recovery-outsider@example.invalid",
            "Recovery outsider",
            "synthetic password",
        )
        team = services.teams.create_team(admin.id, "Recovery operators")
        services.memberships.create_membership(admin.id, member.id, team.id)
        dataset = services.datasets.create_dataset(
            admin.id, "recovery", "Recovery fixture", "Synthetic recovery evidence", team.id
        )
        services.dataset_fields.create_field(
            admin.id, dataset.key, "code", "Code", "string", True, True, True
        )
        services.dataset_fields.create_field(admin.id, dataset.key, "label", "Label", "string")
        record = services.dataset_records.create_record(
            admin.id, dataset.key, {"code": "fixture-1", "label": "Published fixture"}
        )
        current = services.datasets.get_dataset_for_edit(admin.id, dataset.key)
        services.dataset_versions.publish(
            admin.id, dataset.id, services.dataset_versions._today().date(), "", current.revision
        )
        services.dataset_versions.create_concept(member.id, dataset.id)
        services.dataset_records.update_record(
            member.id,
            dataset.key,
            record.id,
            1,
            {"code": "fixture-1", "label": "Concept fixture"},
        )
        with sessions() as session:
            audit_count = session.scalar(select(text("count(*)")).select_from(AuditEvent))
        return RecoveryFixture(
            admin.id,
            member.id,
            outsider.id,
            dataset.id,
            dataset.key,
            audit_count,
        )
    finally:
        engine.dispose()


def _snapshot(database_url):
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            return {
                table: connection.scalar(text(f'SELECT count(*) FROM "{table}"'))
                for table in (
                    "users",
                    "memberships",
                    "datasets",
                    "dataset_versions",
                    "dataset_concepts",
                    "audit_events",
                )
            }
    finally:
        engine.dispose()


def _validate_restored_database(database_url, expected: RecoveryFixture, monkeypatch):
    engine, sessions, services = _services(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == "0030_ref_205"
            )
        current = services.dataset_versions.read_current(
            expected.outsider_id, expected.dataset_key, records=True
        )
        assert current.publications[0].name == "Recovery fixture"
        assert {value.field_key: value.value for value in current.records[0].values}["label"] == (
            "Published fixture"
        )
        concept = services.dataset_versions.read(
            expected.member_id, expected.dataset_key, prefer_concept=True
        )
        assert {value.field_key: value.value for value in concept.page.records[0].values}[
            "label"
        ] == ("Concept fixture")
        outsider_view = services.dataset_versions.read(
            expected.outsider_id, expected.dataset_key, prefer_concept=True
        )
        assert {value.field_key: value.value for value in outsider_view.page.records[0].values}[
            "label"
        ] == "Published fixture"
        with pytest.raises(NotFoundError):
            services.dataset_versions.create_concept(expected.outsider_id, expected.dataset_id)
        with sessions() as session:
            assert session.get(DatasetConcept, expected.dataset_id) is not None
            assert (
                session.scalar(select(text("count(*)")).select_from(AuditEvent))
                == expected.audit_count
            )
    finally:
        engine.dispose()
    started = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda application, **_options: started.append(application)
    )
    cli.start(cli.StartConfiguration(database_url, "127.0.0.1", 8000))
    assert len(started) == 1


def _sqlite_backup(source: Path, destination: Path):
    if destination.exists():
        raise RuntimeError("Recovery target must be new and empty")
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as target:
        source_connection.backup(target)


def test_sqlite_backup_restore_preserves_complete_database_and_source(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    source_url = f"sqlite:///{source}"
    expected = _seed_representative_data(source_url)
    before = _snapshot(source_url)

    _sqlite_backup(source, backup)
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        with sqlite3.connect(restored) as target:
            connection.backup(target)
    with sqlite3.connect(restored) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    _validate_restored_database(f"sqlite:///{restored}", expected, monkeypatch)
    assert _snapshot(source_url) == before


def test_sqlite_failed_restore_is_secret_free_and_leaves_source_unchanged(tmp_path):
    source = tmp_path / "source.db"
    source_url = f"sqlite:///{source}"
    _seed_representative_data(source_url)
    before = _snapshot(source_url)
    occupied = tmp_path / "occupied.db"
    occupied.touch()

    with pytest.raises(RuntimeError, match="Recovery target must be new and empty") as failure:
        _sqlite_backup(source, occupied)

    assert "synthetic password" not in str(failure.value)
    assert _snapshot(source_url) == before


def _postgres_cli_url(url: URL, database: str | None = None) -> str:
    return URL.create(
        "postgresql",
        username=url.username,
        host=url.host,
        port=url.port,
        database=database or url.database,
        query=url.query,
    ).render_as_string()


def _postgres_environment(url: URL):
    environment = os.environ.copy()
    if url.password:
        environment["PGPASSWORD"] = url.password
    return environment


def _postgres_command(arguments, *, url: URL, error: str):
    result = subprocess.run(
        arguments,
        env=_postgres_environment(url),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip()
        if url.password:
            detail = detail.replace(url.password, "<redacted>")
        raise RuntimeError(f"{error}: {detail}" if detail else error)


@pytest.fixture
def postgres_recovery_databases():
    configured = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("REFRNCR_TEST_POSTGRES_URL is not configured")
    for executable in ("pg_dump", "pg_restore"):
        if not shutil.which(executable):
            pytest.fail(f"PostgreSQL recovery prerequisite is unavailable: {executable}")
    base = make_url(configured)
    source_name = f"recovery_source_{uuid.uuid4().hex}"
    target_name = f"recovery_target_{uuid.uuid4().hex}"
    administration_url = base.set(database="postgres")
    administration = create_engine(administration_url, isolation_level="AUTOCOMMIT")
    with administration.connect() as connection:
        server_major = int(connection.scalar(text("SHOW server_version_num"))) // 10000
        client_version = subprocess.run(
            ["pg_dump", "--version"], capture_output=True, text=True, check=True
        ).stdout
        client_major = int(client_version.removeprefix("pg_dump (PostgreSQL) ").split(".", 1)[0])
        assert server_major == 16
        assert client_major == server_major
        connection.execute(text(f'CREATE DATABASE "{source_name}" TEMPLATE template0'))
        connection.execute(text(f'CREATE DATABASE "{target_name}" TEMPLATE template0'))
    try:
        yield base.set(database=source_name), base.set(database=target_name)
    finally:
        with administration.connect() as connection:
            for name in (target_name, source_name):
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid <> pg_backend_pid()"
                    ),
                    {"name": name},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        administration.dispose()


def test_postgresql_logical_backup_restore_preserves_complete_database_and_source(
    tmp_path, postgres_recovery_databases, monkeypatch
):
    source_url, target_url = postgres_recovery_databases
    expected = _seed_representative_data(source_url)
    before = _snapshot(source_url)
    archive = tmp_path / "recovery.dump"

    _postgres_command(
        ["pg_dump", "--format=custom", "--file", str(archive), _postgres_cli_url(source_url)],
        url=source_url,
        error="PostgreSQL backup failed; verify connectivity, permissions, and tool compatibility",
    )
    _postgres_command(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            _postgres_cli_url(target_url),
            str(archive),
        ],
        url=target_url,
        error="PostgreSQL restore failed; keep the source active and inspect the disposable target",
    )

    target_engine = create_database_engine(target_url)
    try:
        with target_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'")
                )
                > 0
            )
    finally:
        target_engine.dispose()
    _validate_restored_database(target_url, expected, monkeypatch)
    assert _snapshot(source_url) == before


def test_postgresql_failed_restore_is_secret_free_and_leaves_source_unchanged(
    tmp_path, postgres_recovery_databases
):
    source_url, target_url = postgres_recovery_databases
    _seed_representative_data(source_url)
    before = _snapshot(source_url)
    invalid_archive = tmp_path / "invalid.dump"
    invalid_archive.write_bytes(b"synthetic invalid archive")

    with pytest.raises(RuntimeError, match="keep the source active") as failure:
        _postgres_command(
            [
                "pg_restore",
                "--exit-on-error",
                "--dbname",
                _postgres_cli_url(target_url),
                str(invalid_archive),
            ],
            url=target_url,
            error=(
                "PostgreSQL restore failed; keep the source active and inspect "
                "the disposable target"
            ),
        )

    assert source_url.password not in str(failure.value)
    assert _snapshot(source_url) == before
