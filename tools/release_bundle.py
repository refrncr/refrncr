"""Validate the exact file sets used by the controlled release workflow."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path, PurePosixPath

CHECKSUM_LINE = re.compile(r"^[0-9a-f]{64} [ *](.+)$")


class ReleaseBundleError(ValueError):
    """Raised when a release directory or transferred bundle is not exact."""


def require_exact_distributions(directory: Path) -> None:
    """Require exactly one regular wheel and one regular sdist."""
    entries = list(directory.iterdir())
    symlinks = [path.name for path in entries if path.is_symlink()]
    files = [path for path in entries if path.is_file() and not path.is_symlink()]
    wheels = [path for path in files if path.name.endswith(".whl")]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    if symlinks or len(entries) != 2 or len(wheels) != 1 or len(sdists) != 1:
        names = ", ".join(sorted(path.name for path in entries)) or "<empty>"
        raise ReleaseBundleError(
            "distribution directory must contain exactly one regular wheel and one regular "
            f"source distribution; found: {names}"
        )


def _manifest_entries(manifest: Path) -> set[str]:
    entries: set[str] = set()
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        match = CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise ReleaseBundleError(f"invalid checksum line {line_number}")
        value = PurePosixPath(match.group(1))
        if value.is_absolute() or ".." in value.parts or value == PurePosixPath("."):
            raise ReleaseBundleError(f"unsafe checksum path on line {line_number}")
        normalized = value.as_posix()
        if normalized in entries:
            raise ReleaseBundleError(f"duplicate checksum path: {normalized}")
        entries.add(normalized)
    if not entries:
        raise ReleaseBundleError("checksum manifest is empty")
    return entries


def _bundle_files(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    symlinks: set[str] = set()
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in file_names:
            path = current / name
            relative = path.relative_to(root).as_posix()
            files.add(relative)
            if path.is_symlink():
                symlinks.add(relative)
        for name in directory_names:
            path = current / name
            if path.is_symlink():
                relative = path.relative_to(root).as_posix()
                files.add(relative)
                symlinks.add(relative)
    return files, symlinks


def verify_bundle_inventory(root: Path, manifest: Path) -> None:
    """Require the recursive bundle inventory to equal its manifest plus the manifest itself."""
    root = root.resolve()
    manifest = manifest.resolve()
    try:
        manifest_name = manifest.relative_to(root).as_posix()
    except ValueError as error:
        raise ReleaseBundleError("checksum manifest must be inside the bundle") from error
    expected = _manifest_entries(manifest) | {manifest_name}
    actual, symlinks = _bundle_files(root)
    if symlinks:
        raise ReleaseBundleError("bundle contains symlinks: " + ", ".join(sorted(symlinks)))
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)))
        if extra:
            details.append("extra: " + ", ".join(sorted(extra)))
        raise ReleaseBundleError("bundle inventory mismatch; " + "; ".join(details))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    distributions = subparsers.add_parser("distributions")
    distributions.add_argument("directory", type=Path)
    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("root", type=Path)
    inventory.add_argument("manifest", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "distributions":
            require_exact_distributions(arguments.directory)
        else:
            verify_bundle_inventory(arguments.root, arguments.manifest)
    except (OSError, ReleaseBundleError) as error:
        print(f"release bundle validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
