from pathlib import Path

import pytest

from tools.dependency_pins import DependencyPinError, read_dependency_pins
from tools.sync_dependency_pins import (
    PinSynchronizationError,
    main,
    synchronize_dependency_pins,
)


def write_repository(root: Path) -> None:
    (root / "requirements").mkdir()
    (root / ".github/workflows").mkdir(parents=True)
    (root / "requirements/dev.in").write_text(
        "build==1.6.1\ncyclonedx-bom==9.0.0\npip-audit==2.10.1\n"
        "pip-tools==7.7.0\npipx==1.7.1\nruff==0.17.5\ntwine==7.0.0\n"
    )
    (root / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v6.0.0\n"
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.17.4\n"
        "    hooks:\n"
        "      - id: ruff-check\n"
    )
    (root / ".github/workflows/security.yml").write_text(
        "jobs:\n"
        "  dependency-and-policy:\n"
        "    steps:\n"
        "      - name: Install security tooling\n"
        "        run: python -m pip install pip-audit==2.9.0 pip-tools==7.6.2\n"
        "      - name: Verify lock\n"
        "        run: python tools/compile_release_lock.py\n"
    )
    (root / ".github/workflows/release.yml").write_text(
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - name: Install locked runtime graph and release tools\n"
        "        run: python -m pip install build==1.6.0 cyclonedx-bom==8.0.1 "
        "pip-audit==2.9.0 pipx==1.6.0 twine==6.2.0\n"
        "      - name: Build\n"
        "        run: python -m build\n"
    )


def repository_contents(root: Path) -> dict[Path, str]:
    return {path.relative_to(root): path.read_text() for path in root.rglob("*") if path.is_file()}


def test_sync_updates_all_managed_pins(tmp_path):
    write_repository(tmp_path)

    changed = synchronize_dependency_pins(tmp_path)

    assert {path.relative_to(tmp_path) for path in changed} == {
        Path(".pre-commit-config.yaml"),
        Path(".github/workflows/security.yml"),
        Path(".github/workflows/release.yml"),
    }
    pre_commit = (tmp_path / ".pre-commit-config.yaml").read_text()
    assert "rev: v6.0.0" in pre_commit
    assert "rev: v0.17.5" in pre_commit
    security = (tmp_path / ".github/workflows/security.yml").read_text()
    assert "pip-audit==2.10.1 pip-tools==7.7.0" in security
    release = (tmp_path / ".github/workflows/release.yml").read_text()
    assert "build==1.6.1" in release
    assert "cyclonedx-bom==9.0.0" in release
    assert "twine==7.0.0" in release
    assert "pip-audit==2.10.1" in release
    assert "pipx==1.7.1" in release


@pytest.mark.parametrize(
    ("relative_path", "old", "new"),
    [
        (".github/workflows/security.yml", "pip-audit==2.10.1", "pip-audit==2.9.0"),
        (".github/workflows/release.yml", "pip-audit==2.10.1", "pip-audit==2.9.0"),
        (".github/workflows/release.yml", "pipx==1.7.1", "pipx==1.6.0"),
    ],
)
def test_check_reports_stale_managed_workflow_pin(tmp_path, relative_path, old, new):
    write_repository(tmp_path)
    synchronize_dependency_pins(tmp_path)
    target = tmp_path / relative_path
    target.write_text(target.read_text().replace(old, new))

    assert target in synchronize_dependency_pins(tmp_path, check=True)


def test_sync_is_idempotent(tmp_path):
    write_repository(tmp_path)
    synchronize_dependency_pins(tmp_path)
    after_first_run = repository_contents(tmp_path)

    assert synchronize_dependency_pins(tmp_path) == []
    assert repository_contents(tmp_path) == after_first_run


def test_check_reports_changes_without_writing(tmp_path):
    write_repository(tmp_path)
    original = repository_contents(tmp_path)

    changed = synchronize_dependency_pins(tmp_path, check=True)

    assert len(changed) == 3
    assert repository_contents(tmp_path) == original


def test_check_cli_points_to_repair_command(tmp_path, monkeypatch, capsys):
    write_repository(tmp_path)
    monkeypatch.setattr("tools.sync_dependency_pins.ROOT", tmp_path)

    assert main(["--check"]) == 1
    assert "python tools/sync_dependency_pins.py" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("relative_path", "old", "new"),
    [
        (".pre-commit-config.yaml", "    rev: v0.17.4\n", "    tag: v0.17.4\n"),
        (
            ".github/workflows/security.yml",
            "pip-audit==2.9.0 pip-tools==7.6.2",
            "pip-audit==2.9.0 piptools==7.6.2",
        ),
        (
            ".github/workflows/release.yml",
            "cyclonedx-bom==8.0.1",
            "cyclonedx-bom~=8.0.1",
        ),
    ],
)
def test_sync_rejects_missing_or_unknown_target_without_partial_writes(
    tmp_path, relative_path, old, new
):
    write_repository(tmp_path)
    target = tmp_path / relative_path
    target.write_text(target.read_text().replace(old, new))
    original = repository_contents(tmp_path)

    with pytest.raises(PinSynchronizationError, match="expected one"):
        synchronize_dependency_pins(tmp_path)

    assert repository_contents(tmp_path) == original


@pytest.mark.parametrize(
    "source",
    [
        "build==1.6.1\ncyclonedx-bom==9.0.0\nruff==0.17.5\ntwine==7.0.0\n",
        (
            "build==1.6.1\ncyclonedx-bom==9.0.0\npip-tools==7.7.0\n"
            "pip-tools==7.6.2\nruff==0.17.5\ntwine==7.0.0\n"
        ),
        ("build==1.6.1\ncyclonedx-bom==9.0.0\npip-tools>=7.7.0\nruff==0.17.5\ntwine==7.0.0\n"),
    ],
)
def test_sync_rejects_missing_duplicate_or_inexact_source_pin(tmp_path, source):
    write_repository(tmp_path)
    (tmp_path / "requirements/dev.in").write_text(source)

    with pytest.raises(DependencyPinError, match="pip-tools"):
        synchronize_dependency_pins(tmp_path)


def test_shared_pin_reader_requires_one_exact_pin(tmp_path):
    source = tmp_path / "dev.in"
    source.write_text("pip-tools==7.7.0\n")
    assert read_dependency_pins(source, ("pip-tools",)) == {"pip-tools": "7.7.0"}

    source.write_text("pip-tools>=7.7.0\n")
    with pytest.raises(DependencyPinError, match="pip-tools"):
        read_dependency_pins(source, ("pip-tools",))
