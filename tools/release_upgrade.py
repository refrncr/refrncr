"""For stable releases, prove upgrade and representative-data preservation."""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import venv
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.version import Version


def wheel_identity(wheel: Path) -> tuple[str, Version]:
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError(f"expected one wheel METADATA file, found {len(names)}")
        metadata = BytesParser().parsebytes(archive.read(names[0]))
    name = metadata.get("Name")
    raw_version = metadata.get("Version")
    if not name or not raw_version:
        raise ValueError("wheel metadata must contain Name and Version")
    return name, Version(raw_version)


def preceding_public_version(name: str, current: Version) -> Version:
    encoded_name = urllib.parse.quote(name, safe="")
    url = f"https://pypi.org/pypi/{encoded_name}/json"
    with urllib.request.urlopen(url, timeout=15) as response:
        releases = json.load(response)["releases"]
    candidates = [
        Version(raw) for raw, files in releases.items() if files and Version(raw) < current
    ]
    if not candidates:
        raise RuntimeError(f"no preceding public release exists for stable version {current}")
    return max(candidates)


def executable(environment: Path, name: str) -> Path:
    directory = environment / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    return directory / f"{name}{suffix}"


def run_checked(arguments: list[str], *, cwd: Path) -> None:
    subprocess.run(arguments, cwd=cwd, check=True)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_health(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while True:
        if process.poll() is not None:
            raise RuntimeError("upgraded application exited before becoming healthy")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                if response.status == 200 and response.read() == b'{"status":"ok"}':
                    return
        except (OSError, urllib.error.URLError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("upgraded application did not become healthy")
        time.sleep(0.2)


def create_representative_database(python: Path, database: Path, working: Path) -> None:
    driver = working / "create_representative_data.py"
    driver.write_text(
        "from refrncr import cli\n"
        "answers = iter(['temporary release verification password', "
        "'temporary release verification password'])\n"
        "raise SystemExit(cli.main([\n"
        "    'user', 'create-admin',\n"
        "    '--email', 'upgrade-verification@example.invalid',\n"
        "    '--display-name', 'Upgrade Verification Administrator',\n"
        f"    '--database-path', {str(database)!r},\n"
        "], password_reader=lambda _prompt: next(answers)))\n",
        encoding="utf-8",
    )
    run_checked([str(python), str(driver)], cwd=working)


def verify_preserved_data(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT email, display_name, role, is_active, is_deleted FROM users"
        ).fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    expected = (
        "upgrade-verification@example.invalid",
        "Upgrade Verification Administrator",
        "admin",
        1,
        0,
    )
    if row != expected or revision is None or not revision[0]:
        raise RuntimeError("representative data or migration state was not preserved")


def verify_upgrade(wheel: Path, working: Path) -> None:
    wheel = wheel.resolve()
    name, current = wheel_identity(wheel)
    if current.is_prerelease:
        print(f"Upgrade gate is not required for pre-release {current}")
        return

    previous = preceding_public_version(name, current)
    working.mkdir(parents=True, exist_ok=False)
    environment = working / "environment"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = executable(environment, "python")
    application = executable(environment, "refrncr")
    database = working / "upgrade.db"

    run_checked([str(python), "-m", "pip", "install", f"{name}=={previous}"], cwd=working)
    create_representative_database(python, database, working)
    run_checked([str(python), "-m", "pip", "install", "--upgrade", str(wheel)], cwd=working)

    port = free_port()
    process = subprocess.Popen(
        [str(application), "start", "--database-path", str(database), "--port", str(port)],
        cwd=working,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        wait_for_health(port, process)
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.returncode != 0:
        raise RuntimeError("upgraded application did not stop cleanly")
    verify_preserved_data(database)
    print(f"Verified upgrade from {previous} to {current} with representative data preserved")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: release_upgrade.py WHEEL WORKING_DIRECTORY")
    verify_upgrade(Path(sys.argv[1]), Path(sys.argv[2]))
