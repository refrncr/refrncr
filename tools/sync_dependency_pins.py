"""Synchronize derived dependency pins using only the Python standard library."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .dependency_pins import DependencyPinError, read_dependency_pins
else:
    from dependency_pins import DependencyPinError, read_dependency_pins

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGES = ("ruff", "pip-tools", "build", "cyclonedx-bom", "twine")
REPAIR_COMMAND = "python tools/sync_dependency_pins.py"


class PinSynchronizationError(ValueError):
    """Raised when a derived pin cannot be identified safely."""


@dataclass(frozen=True)
class PendingWrite:
    path: Path
    original: str
    updated: str


def _job_block(content: str, path: Path, job_name: str) -> tuple[int, int]:
    pattern = re.compile(rf"(?m)^  {re.escape(job_name)}:\s*$")
    starts = [match.start() for match in pattern.finditer(content)]
    if len(starts) != 1:
        raise PinSynchronizationError(
            f"{path}: expected one job named {job_name!r}; found {len(starts)}"
        )
    start = starts[0]
    next_job = re.search(r"(?m)^  [A-Za-z0-9_-]+:\s*$", content[start + 1 :])
    end = len(content) if next_job is None else start + 1 + next_job.start()
    return start, end


def _step_block(content: str, path: Path, step_name: str) -> tuple[int, int]:
    marker = f"      - name: {step_name}"
    starts = [match.start() for match in re.finditer(re.escape(marker), content)]
    if len(starts) != 1:
        raise PinSynchronizationError(
            f"{path}: expected one step named {step_name!r}; found {len(starts)}"
        )
    start = starts[0]
    next_step = content.find("\n      - name:", start + len(marker))
    return start, len(content) if next_step == -1 else next_step


def _repo_block(content: str, path: Path, repository: str) -> tuple[int, int]:
    marker = f"  - repo: {repository}"
    starts = [match.start() for match in re.finditer(re.escape(marker), content)]
    if len(starts) != 1:
        raise PinSynchronizationError(
            f"{path}: expected one repository block for {repository}; found {len(starts)}"
        )
    start = starts[0]
    next_repo = content.find("\n  - repo:", start + len(marker))
    return start, len(content) if next_repo == -1 else next_repo


def _replace_package_pin(block: str, path: Path, package: str, version: str) -> str:
    pattern = re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(package)}==[^\s]+")
    updated, count = pattern.subn(f"{package}=={version}", block)
    if count != 1:
        raise PinSynchronizationError(
            f"{path}: expected one {package}== pin in the recognized block; found {count}"
        )
    return updated


def _replace_block(content: str, bounds: tuple[int, int], updated_block: str) -> str:
    start, end = bounds
    return content[:start] + updated_block + content[end:]


def _sync_pre_commit(path: Path, version: str) -> PendingWrite:
    original = path.read_text()
    bounds = _repo_block(original, path, "https://github.com/astral-sh/ruff-pre-commit")
    block = original[bounds[0] : bounds[1]]
    updated_block, count = re.subn(
        r"(?m)^(\s*rev:\s*)v[^\s#]+(\s*(?:#.*)?)$",
        rf"\g<1>v{version}\g<2>",
        block,
    )
    if count != 1:
        raise PinSynchronizationError(
            f"{path}: expected one Ruff rev in the recognized repository block; found {count}"
        )
    return PendingWrite(path, original, _replace_block(original, bounds, updated_block))


def _sync_security(path: Path, version: str) -> PendingWrite:
    original = path.read_text()
    job_bounds = _job_block(original, path, "dependency-and-policy")
    job = original[job_bounds[0] : job_bounds[1]]
    step_bounds = _step_block(job, path, "Install security tooling")
    step = job[step_bounds[0] : step_bounds[1]]
    updated_step = _replace_package_pin(step, path, "pip-tools", version)
    updated_job = _replace_block(job, step_bounds, updated_step)
    return PendingWrite(path, original, _replace_block(original, job_bounds, updated_job))


def _sync_release(path: Path, pins: dict[str, str]) -> PendingWrite:
    original = path.read_text()
    job_bounds = _job_block(original, path, "build")
    job = original[job_bounds[0] : job_bounds[1]]
    step_bounds = _step_block(job, path, "Install locked runtime graph and release tools")
    block = job[step_bounds[0] : step_bounds[1]]
    for package in ("build", "cyclonedx-bom", "twine"):
        block = _replace_package_pin(block, path, package, pins[package])
    updated_job = _replace_block(job, step_bounds, block)
    return PendingWrite(path, original, _replace_block(original, job_bounds, updated_job))


def planned_writes(root: Path) -> list[PendingWrite]:
    """Validate every source and target, then return all pending file contents."""
    pins = read_dependency_pins(root / "requirements/dev.in", SOURCE_PACKAGES)
    return [
        _sync_pre_commit(root / ".pre-commit-config.yaml", pins["ruff"]),
        _sync_security(root / ".github/workflows/security.yml", pins["pip-tools"]),
        _sync_release(root / ".github/workflows/release.yml", pins),
    ]


def synchronize_dependency_pins(root: Path, *, check: bool = False) -> list[Path]:
    """Synchronize derived pins, returning paths whose contents differ."""
    writes = planned_writes(root)
    changed = [write.path for write in writes if write.updated != write.original]
    if not check:
        for write in writes:
            if write.updated != write.original:
                write.path.write_text(write.updated)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report stale pins without writing")
    arguments = parser.parse_args(argv)
    try:
        changed = synchronize_dependency_pins(ROOT, check=arguments.check)
    except (DependencyPinError, PinSynchronizationError, OSError) as error:
        print(f"dependency pin synchronization failed: {error}", file=sys.stderr)
        print(f"Review the inputs, then run: {REPAIR_COMMAND}", file=sys.stderr)
        return 2
    if arguments.check and changed:
        paths = ", ".join(str(path.relative_to(ROOT)) for path in changed)
        print(f"derived dependency pins are stale in: {paths}", file=sys.stderr)
        print(f"Run: {REPAIR_COMMAND}", file=sys.stderr)
        return 1
    if not arguments.check and changed:
        for path in changed:
            print(f"updated {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
