import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import tarfile
import time
import urllib.error
import urllib.request
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import URL, create_engine, inspect, text


@dataclass(frozen=True)
class InstalledArtifact:
    executable: Path
    python: Path
    wheel: Path
    sdist: Path
    working_directory: Path
    install_directory: Path


@pytest.fixture(scope="session")
def installed_artifact(tmp_path_factory):
    root = tmp_path_factory.mktemp("installed-artifact")
    source = root / "build source"
    source.mkdir()
    repository = Path(__file__).resolve().parents[1]
    shutil.copy(repository / "pyproject.toml", source)
    shutil.copy(repository / "README.md", source)
    shutil.copytree(
        repository / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__")
    )

    wheel_directory = root / "dist"
    wheel_directory.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import setuptools.build_meta as backend, sys; backend.build_sdist(sys.argv[1])",
            str(wheel_directory),
        ],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    )
    sdist = next(wheel_directory.glob("refrncr-*.tar.gz"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_directory),
        ],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_directory.glob("refrncr-*.whl"))

    environment = root / "isolated environment"
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_directory = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bin_directory / ("python.exe" if os.name == "nt" else "python")
    executable = bin_directory / ("refrncr.exe" if os.name == "nt" else "refrncr")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    installed_site = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    Path(installed_site, "refrncr-test-dependencies.pth").write_text(
        f"{sysconfig.get_path('purelib')}\n"
    )
    working_directory = root / "unrelated working directory"
    working_directory.mkdir()

    imported_from = subprocess.run(
        [str(python), "-c", "import refrncr; print(refrncr.__file__)"],
        cwd=working_directory,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(imported_from).is_relative_to(environment)

    return InstalledArtifact(executable, python, wheel, sdist, working_directory, environment)


def clean_environment():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    return environment


def free_loopback_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.slow
def test_wheel_contains_migration_runtime(installed_artifact):
    with zipfile.ZipFile(installed_artifact.wheel) as archive:
        files = set(archive.namelist())

    assert "refrncr/migrations/env.py" in files
    assert "refrncr/migrations/script.py.mako" in files
    assert {
        "refrncr/migrations/versions/0021_ref_40_api_keys.py",
        "refrncr/migrations/versions/0022_ref_31_security_audit.py",
        "refrncr/migrations/versions/0023_ref_176_effective_dates.py",
        "refrncr/migrations/versions/0024_ref_177_mapping_tables.py",
        "refrncr/migrations/versions/0025_ref_177_mapping_fields.py",
        "refrncr/migrations/versions/0026_ref_177_mapping_field_roles.py",
        "refrncr/migrations/versions/0027_ref_177_standalone_source_context.py",
        "refrncr/migrations/versions/0028_ref_179_hierarchies.py",
        "refrncr/migrations/versions/0029_ref_195_dark_mode.py",
        "refrncr/migrations/versions/0030_ref_205_typed_draft_values.py",
        "refrncr/migrations/versions/0031_ref_239_mapping_source_versions.py",
        "refrncr/migrations/versions/0032_ref_239_source_version_policy.py",
        "refrncr/api_keys.py",
        "refrncr/api_key_persistence.py",
        "refrncr/static/js/api-key-secret.js",
        "refrncr/static/css/layouts/api-keys.css",
        "refrncr/templates/pages/api_keys/base.html",
        "refrncr/templates/pages/api_keys/list.html",
        "refrncr/templates/pages/api_keys/create.html",
        "refrncr/templates/pages/api_keys/secret.html",
        "refrncr/templates/pages/api_keys/revoke.html",
        "refrncr/migrations/versions/0001_ref_33_community_users.py",
        "refrncr/migrations/versions/0002_ref_34_community_teams.py",
        "refrncr/migrations/versions/0003_ref_35_team_memberships.py",
        "refrncr/migrations/versions/0004_ref_36_access_grants.py",
    } <= files
    assert {
        "refrncr/static/css/refrncr.css",
        "refrncr/static/css/tokens.css",
        "refrncr/static/css/base.css",
        "refrncr/static/css/utilities.css",
        "refrncr/static/css/layouts/app-shell.css",
        "refrncr/static/css/layouts/page.css",
        "refrncr/static/css/layouts/auth.css",
        "refrncr/static/css/components/buttons.css",
        "refrncr/static/css/components/forms.css",
        "refrncr/static/css/components/select.css",
        "refrncr/static/js/select.js",
        "refrncr/static/css/components/feedback.css",
        "refrncr/static/css/components/cards-badges.css",
        "refrncr/static/css/components/tables.css",
        "refrncr/static/css/components/dialog.css",
        "refrncr/static/fonts/instrument-sans/instrument-sans-latin-500-normal.woff2",
        "refrncr/static/fonts/instrument-sans/instrument-sans-latin-600-normal.woff2",
        "refrncr/static/fonts/ibm-plex-sans/ibm-plex-sans-latin-400-normal.woff2",
        "refrncr/static/fonts/ibm-plex-sans/ibm-plex-sans-latin-600-normal.woff2",
        "refrncr/static/fonts/ibm-plex-sans/ibm-plex-sans-latin-700-normal.woff2",
        "refrncr/static/fonts/ibm-plex-mono/ibm-plex-mono-latin-400-normal.woff2",
        "refrncr/static/licenses/OFL-Instrument-Sans.txt",
        "refrncr/static/licenses/OFL-IBM-Plex-Sans.txt",
        "refrncr/static/licenses/OFL-IBM-Plex-Mono.txt",
        "refrncr/static/licenses/LICENSE-Lucide.txt",
        "refrncr/static/licenses/ASSET-SOURCES.md",
    } <= files
    assert {
        "refrncr/templates/layouts/auth/base.html",
        "refrncr/templates/pages/auth/login.html",
        "refrncr/templates/pages/auth/setup.html",
        "refrncr/templates/pages/datasets/create.html",
        "refrncr/templates/pages/datasets/detail.html",
        "refrncr/templates/pages/datasets/_edit_form.html",
        "refrncr/templates/pages/datasets/list.html",
        "refrncr/templates/pages/datasets/_record_table.html",
        "refrncr/templates/pages/datasets/_record_form_fields.html",
        "refrncr/templates/pages/datasets/_record_panels.html",
        "refrncr/templates/pages/datasets/records.html",
        "refrncr/templates/components/side_panel.html",
        "refrncr/templates/pages/users/list.html",
        "refrncr/templates/pages/users/_user_form_fields.html",
        "refrncr/templates/pages/users/_user_panels.html",
        "refrncr/templates/pages/teams/list.html",
        "refrncr/templates/pages/teams/members.html",
        "refrncr/templates/pages/teams/_membership_controls.html",
        "refrncr/templates/pages/teams/_team_form_fields.html",
        "refrncr/templates/pages/teams/_team_panels.html",
        "refrncr/templates/pages/datasets/import/upload.html",
        "refrncr/templates/pages/datasets/import/worksheet.html",
        "refrncr/templates/pages/datasets/import/mapping.html",
        "refrncr/templates/pages/datasets/import/preview.html",
        "refrncr/templates/pages/datasets/records/form.html",
        "refrncr/templates/pages/datasets/records/delete.html",
        "refrncr/templates/pages/datasets/fields/_form_fields.html",
        "refrncr/templates/pages/api/docs.html",
        "refrncr/static/js/swagger-ui-bundle.js",
        "refrncr/static/js/api-docs.js",
        "refrncr/static/css/swagger-ui.css",
        "refrncr/static/css/layouts/api-docs.css",
        "refrncr/static/licenses/LICENSE-Swagger-UI.txt",
        "refrncr/templates/pages/datasets/fields/create.html",
        "refrncr/templates/pages/datasets/fields/edit.html",
    } <= files
    assert not any(name.endswith((".db", ".pyc")) or "__pycache__" in name for name in files)


@pytest.mark.slow
def test_sdist_contains_ui_runtime_and_licenses(installed_artifact):
    with tarfile.open(installed_artifact.sdist) as archive:
        files = {name.split("/", 1)[-1] for name in archive.getnames()}

    assert {
        "src/refrncr/static/css/components/dialog.css",
        "src/refrncr/static/css/layouts/app-shell.css",
        "src/refrncr/static/fonts/ibm-plex-sans/ibm-plex-sans-latin-400-normal.woff2",
        "src/refrncr/static/fonts/ibm-plex-mono/ibm-plex-mono-latin-400-normal.woff2",
        "src/refrncr/static/fonts/instrument-sans/instrument-sans-latin-600-normal.woff2",
        "src/refrncr/static/licenses/ASSET-SOURCES.md",
        "src/refrncr/static/licenses/LICENSE-Lucide.txt",
        "src/refrncr/templates/pages/datasets/records.html",
    } <= files


@pytest.mark.slow
def test_installed_console_help_and_http_startup(installed_artifact):
    for arguments in (
        ["--help"],
        ["start", "--help"],
        ["user", "--help"],
        ["user", "create-admin", "--help"],
    ):
        result = subprocess.run(
            [str(installed_artifact.executable), *arguments],
            cwd=installed_artifact.working_directory,
            env=clean_environment(),
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        if arguments[0] == "user":
            assert "create-admin" in result.stdout or "first administrator" in result.stdout
        else:
            assert "start" in result.stdout
        if arguments == ["start", "--help"]:
            assert "--log-level" in result.stdout

    database = installed_artifact.working_directory / "installed ? smoke.db"
    port = free_loopback_port()
    process = subprocess.Popen(
        [
            str(installed_artifact.executable),
            "start",
            "--database-path",
            str(database),
            "--port",
            str(port),
        ],
        cwd=installed_artifact.working_directory,
        env=clean_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"installed server exited early:\n{stdout}\n{stderr}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as response:
                    body = response.read().decode()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail("installed server did not become ready")
                time.sleep(0.05)
        assert response.status == 200
        assert "refrncr" in body
        for probe in ("healthz", "readyz"):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/{probe}", timeout=3
            ) as probe_response:
                assert probe_response.status == 200
                assert probe_response.read() == b'{"status":"ok"}'
                assert probe_response.headers["Content-Type"] == "application/json"
                assert probe_response.headers["Cache-Control"] == "no-store"
                assert probe_response.headers["X-Request-ID"]
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/datasets", timeout=1)
        assert denied.value.code == 401
        assert denied.value.read() == b'{"error":"invalid_api_credentials"}'
        assert denied.value.headers["Vary"] == "Authorization, Accept-Encoding"
        for asset in (
            "api/docs",
            "api/openapi.json",
            "static/js/swagger-ui-bundle.js",
            "static/js/api-docs.js",
            "static/css/swagger-ui.css",
            "static/css/layouts/api-docs.css",
            "static/css/components/dialog.css",
            "static/fonts/instrument-sans/instrument-sans-latin-600-normal.woff2",
            "static/fonts/ibm-plex-sans/ibm-plex-sans-latin-400-normal.woff2",
            "static/fonts/ibm-plex-sans/ibm-plex-sans-latin-600-normal.woff2",
            "static/fonts/ibm-plex-sans/ibm-plex-sans-latin-700-normal.woff2",
            "static/fonts/ibm-plex-mono/ibm-plex-mono-latin-400-normal.woff2",
        ):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/{asset}", timeout=1
            ) as asset_response:
                assert asset_response.status == 200
    finally:
        if process.poll() is None:
            if os.name == "nt":
                process.terminate()
            else:
                process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0
    stdout, stderr = process.communicate()
    log_lines = [json.loads(line) for line in stdout.splitlines() if line]
    assert log_lines
    assert all(set(line) >= {"timestamp", "severity", "event"} for line in log_lines)
    assert any(
        line.get("event") == "http.request_completed" and line.get("route") == "/healthz"
        for line in log_lines
    )
    assert any(
        line.get("event") == "http.request_completed" and line.get("route") == "/readyz"
        for line in log_lines
    )
    assert "installed ? smoke.db" not in stdout
    assert "installed ? smoke.db" not in stderr
    assert database.is_file()
    engine = create_engine(URL.create("sqlite", database=str(database)))
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_installed_artifact_bootstraps_admin_with_injected_password_reader(
    installed_artifact,
):
    database = installed_artifact.working_directory / "installed bootstrap.db"
    password = "installed artifact secure password"
    driver = installed_artifact.working_directory / "bootstrap_driver.py"
    driver.write_text(
        "from refrncr import cli\n"
        f"answers = iter([{password!r}, {password!r}])\n"
        "raise SystemExit(cli.main([\n"
        "    'user', 'create-admin',\n"
        "    '--email', ' Installed@Example.COM ',\n"
        "    '--display-name', ' Installed Administrator ',\n"
        f"    '--database-path', {str(database)!r},\n"
        "], password_reader=lambda _prompt: next(answers)))\n"
    )

    result = subprocess.run(
        [str(installed_artifact.python), str(driver)],
        cwd=installed_artifact.working_directory,
        env=clean_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "First Community administrator created" in result.stdout
    assert password not in result.stdout
    assert password not in result.stderr
    engine = create_engine(URL.create("sqlite", database=str(database)))
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT email, display_name, role, is_active, is_deleted, password_hash "
                    "FROM users"
                )
            ).one()
        assert row[:5] == (
            "installed@example.com",
            "Installed Administrator",
            "admin",
            True,
            False,
        )
        assert row.password_hash.startswith("$argon2id$")
        assert row.password_hash not in result.stdout
        assert row.password_hash not in result.stderr
    finally:
        engine.dispose()


def test_real_port_conflict_exits_nonzero(installed_artifact):
    database = installed_artifact.working_directory / "credential-secret.db"
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        result = subprocess.run(
            [
                str(installed_artifact.executable),
                "start",
                "--database-path",
                str(database),
                "--port",
                str(port),
            ],
            cwd=installed_artifact.working_directory,
            env=clean_environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

    assert result.returncode != 0
    lines = [json.loads(line) for line in result.stdout.splitlines() if line]
    assert any(
        line.get("event") == "runtime.failure" and line.get("error_category") == "address_in_use"
        for line in lines
    )
    assert "credential-secret" not in result.stderr
    assert "credential-secret" not in result.stdout
