"""Read exact dependency pins from a requirements input file."""

from __future__ import annotations

import re
from pathlib import Path

EXACT_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


class DependencyPinError(ValueError):
    """Raised when a required dependency pin is missing or ambiguous."""


def read_dependency_pins(path: Path, packages: tuple[str, ...]) -> dict[str, str]:
    """Return exactly pinned versions for the requested package names."""
    matches: dict[str, list[str]] = {package: [] for package in packages}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = EXACT_PIN.fullmatch(line.strip())
        if match and match.group(1) in matches:
            matches[match.group(1)].append(match.group(2))

    invalid = [package for package, versions in matches.items() if len(versions) != 1]
    if invalid:
        details = ", ".join(
            f"{package} ({len(matches[package])} exact pins)" for package in invalid
        )
        raise DependencyPinError(f"{path}: expected exactly one exact pin for {details}")
    return {package: versions[0] for package, versions in matches.items()}
