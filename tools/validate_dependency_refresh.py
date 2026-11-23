"""Validate Dependabot refresh decisions and patches using the standard library."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__:
    from .sync_dependency_pins import planned_writes
else:
    from sync_dependency_pins import planned_writes

ALLOWED_PATHS = frozenset(
    {
        ".github/workflows/release.yml",
        ".github/workflows/security.yml",
        ".pre-commit-config.yaml",
        "requirements/dev.txt",
        "requirements/release.txt",
    }
)
DERIVED_PATHS = (
    ".pre-commit-config.yaml",
    ".github/workflows/security.yml",
    ".github/workflows/release.yml",
)
DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
INDEX_HEADER = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?$")
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")


class RefreshValidationError(ValueError):
    """Raised when refresh input is unsafe or inconsistent."""


def _patch_path(line: str, paths: set[str]) -> str:
    match = DIFF_HEADER.fullmatch(line)
    if not match or match.group(1) != match.group(2):
        raise RefreshValidationError("patch contains a renamed or unsafe path")
    path = match.group(1)
    if path not in ALLOWED_PATHS:
        raise RefreshValidationError(f"path is not allowlisted: {path}")
    if path in paths:
        raise RefreshValidationError(f"duplicate patch section: {path}")
    return path


def _hunk_counts(line: str, path: str) -> tuple[int, int]:
    match = HUNK_HEADER.fullmatch(line)
    if match is None:
        raise RefreshValidationError(f"invalid hunk header for {path}")
    counts = (int(match.group(2) or 1), int(match.group(4) or 1))
    if counts == (0, 0):
        raise RefreshValidationError(f"empty hunk for {path}")
    return counts


def _consume_hunk_line(line: str, path: str, old: int, new: int) -> tuple[int, int]:
    prefix = line[:1]
    if prefix == " ":
        if old == 0 or new == 0:
            raise RefreshValidationError(f"hunk exceeds declared size for {path}")
        return old - 1, new - 1
    if prefix == "-":
        if old == 0:
            raise RefreshValidationError(f"hunk exceeds declared old size for {path}")
        return old - 1, new
    if prefix == "+":
        if new == 0:
            raise RefreshValidationError(f"hunk exceeds declared new size for {path}")
        return old, new - 1
    raise RefreshValidationError(f"unsupported hunk content for {path}")


def _git(root: Path, *arguments: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=text,
    )
    if result.returncode != 0:
        error = result.stderr.strip() if text else result.stderr.decode(errors="replace").strip()
        raise RefreshValidationError(f"git {' '.join(arguments)} failed: {error}")
    return result.stdout


def prepare_patch(root: Path, patch_path: Path, output_path: Path) -> bool:
    """Write the tracked text patch and its changed decision for the workflow."""
    patch = _git(root, "diff", "--no-ext-diff", "--no-color", text=False)
    assert isinstance(patch, bytes)
    changed = bool(patch)
    if changed:
        patch_path.write_bytes(patch)
    with output_path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"changed={'true' if changed else 'false'}\n")
    return changed


def validate_patch(patch_path: Path) -> set[str]:
    """Reject unsafe patch metadata before the patch is applied."""
    try:
        lines = patch_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RefreshValidationError("patch is not UTF-8 text") from error
    if not lines:
        raise RefreshValidationError("patch is empty")

    paths: set[str] = set()
    current_path: str | None = None
    saw_old = saw_new = saw_hunk = False
    old_remaining = new_remaining = 0
    last_was_hunk_line = False

    def finish_section() -> None:
        if current_path is not None and not (saw_old and saw_new and saw_hunk):
            raise RefreshValidationError(f"incomplete text patch for {current_path}")
        if old_remaining or new_remaining:
            raise RefreshValidationError(f"incomplete hunk for {current_path}")

    for line in lines:
        if old_remaining or new_remaining:
            old_remaining, new_remaining = _consume_hunk_line(
                line, str(current_path), old_remaining, new_remaining
            )
            last_was_hunk_line = True
            continue
        if line == "\\ No newline at end of file":
            if not last_was_hunk_line:
                raise RefreshValidationError(f"misplaced no-newline marker for {current_path}")
            last_was_hunk_line = False
            continue
        last_was_hunk_line = False
        if line.startswith("diff --git "):
            finish_section()
            current_path = _patch_path(line, paths)
            paths.add(current_path)
            saw_old = saw_new = saw_hunk = False
            continue
        if current_path is None:
            raise RefreshValidationError("patch data appears before a file header")
        if line.startswith("@@ "):
            if not (saw_old and saw_new):
                raise RefreshValidationError(f"hunk precedes file headers for {current_path}")
            old_remaining, new_remaining = _hunk_counts(line, current_path)
            saw_hunk = True
            continue
        if saw_hunk:
            raise RefreshValidationError(f"content outside a hunk for {current_path}")
        if INDEX_HEADER.fullmatch(line):
            continue
        if line == f"--- a/{current_path}":
            saw_old = True
            continue
        if line == f"+++ b/{current_path}":
            saw_new = True
            continue
        raise RefreshValidationError(f"unsupported patch metadata for {current_path}: {line}")

    finish_section()
    return paths


def apply_patch(root: Path, patch_path: Path) -> None:
    """Apply only allowlisted paths, independently of patch-provided filenames."""
    includes = tuple(f"--include={path}" for path in sorted(ALLOWED_PATHS))
    _git(root, "apply", *includes, str(patch_path))


def validate_worktree(root: Path, expected_head: str) -> set[str]:
    """Validate applied changes and byte-compare every managed derived file."""
    actual_head = str(_git(root, "rev-parse", "HEAD")).strip()
    if actual_head != expected_head:
        raise RefreshValidationError(
            f"checked-out HEAD changed: expected {expected_head}, found {actual_head}"
        )

    status = str(
        _git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored=matching",
        )
    )
    changed: set[str] = set()
    for line in status.splitlines():
        state, path = line[:2], line[3:]
        if state != " M":
            raise RefreshValidationError(f"unexpected worktree entry: {line}")
        if path not in ALLOWED_PATHS:
            raise RefreshValidationError(f"path is not allowlisted: {path}")
        changed.add(path)
    if not changed:
        raise RefreshValidationError("applied patch produced no tracked changes")

    for relative in ALLOWED_PATHS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise RefreshValidationError(f"allowlisted path is not a regular file: {relative}")
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise RefreshValidationError(
                f"allowlisted path is not UTF-8 text: {relative}"
            ) from error

    with TemporaryDirectory() as temporary_directory:
        baseline = Path(temporary_directory)
        (baseline / "requirements").mkdir()
        (baseline / ".github/workflows").mkdir(parents=True)
        (baseline / "requirements/dev.in").write_text(
            (root / "requirements/dev.in").read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )
        for relative in DERIVED_PATHS:
            original = _git(root, "show", f"HEAD:{relative}")
            assert isinstance(original, str)
            (baseline / relative).write_text(original, encoding="utf-8", newline="\n")
        expected = {
            write.path.relative_to(baseline): write.updated.encode("utf-8")
            for write in planned_writes(baseline)
        }
        for relative in DERIVED_PATHS:
            if (root / relative).read_bytes() != expected[Path(relative)]:
                raise RefreshValidationError(
                    f"derived file has non-allowlisted changes: {relative}"
                )
    return changed


def require_secret_flags(has_client_id: str, has_private_key: str) -> None:
    """Require boolean availability flags without receiving either secret value."""
    missing = []
    if has_client_id != "true":
        missing.append("LOCK_REFRESH_CLIENT_ID")
    if has_private_key != "true":
        missing.append("LOCK_REFRESH_PRIVATE_KEY")
    if missing:
        raise RefreshValidationError(
            "required Dependabot secrets are unavailable: " + ", ".join(missing)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--root", type=Path, required=True)
    prepare.add_argument("--patch", type=Path, required=True)
    prepare.add_argument("--github-output", type=Path, required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--patch", type=Path, required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--root", type=Path, required=True)
    apply.add_argument("--patch", type=Path, required=True)
    worktree = subparsers.add_parser("worktree")
    worktree.add_argument("--root", type=Path, required=True)
    worktree.add_argument("--head-sha", required=True)
    subparsers.add_parser("secrets")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "prepare":
            prepare_patch(arguments.root, arguments.patch, arguments.github_output)
        elif arguments.command == "preflight":
            validate_patch(arguments.patch)
        elif arguments.command == "apply":
            apply_patch(arguments.root, arguments.patch)
        elif arguments.command == "worktree":
            validate_worktree(arguments.root, arguments.head_sha)
        else:
            require_secret_flags(
                os.environ.get("HAS_CLIENT_ID", ""), os.environ.get("HAS_PRIVATE_KEY", "")
            )
    except (OSError, RefreshValidationError) as error:
        print(f"dependency refresh validation failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
