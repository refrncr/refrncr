import os
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import time
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

    return InstalledArtifact(executable, python, wheel, working_directory, environment)


def clean_environment():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    return environment


def free_loopback_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_wheel_contains_migration_runtime(installed_artifact):
    with zipfile.ZipFile(installed_artifact.wheel) as archive:
        files = set(archive.namelist())

    assert "refrncr/migrations/env.py" in files
    assert "refrncr/migrations/script.py.mako" in files
    assert {
        "refrncr/migrations/versions/0001_ref_33_community_users.py",
        "refrncr/migrations/versions/0002_ref_34_community_teams.py",
        "refrncr/migrations/versions/0003_ref_35_team_memberships.py",
        "refrncr/migrations/versions/0004_ref_36_access_grants.py",
    } <= files
    assert {
        "refrncr/templates/layouts/auth/base.html",
        "refrncr/templates/pages/auth/login.html",
        "refrncr/templates/pages/auth/setup.html",
        "refrncr/templates/pages/datasets/create.html",
        "refrncr/templates/pages/datasets/detail.html",
        "refrncr/templates/pages/datasets/edit.html",
        "refrncr/templates/pages/datasets/list.html",
        "refrncr/templates/pages/datasets/_record_table.html",
        "refrncr/templates/pages/datasets/_record_form_fields.html",
        "refrncr/templates/pages/datasets/_record_panels.html",
        "refrncr/templates/pages/datasets/records.html",
        "refrncr/templates/components/side_panel.html",
    } <= files
    assert not any(name.endswith((".db", ".pyc")) or "__pycache__" in name for name in files)


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
    assert "address already in use" in result.stderr.lower()
    assert "credential-secret" not in result.stderr
