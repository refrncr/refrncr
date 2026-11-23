"""Generate the hash-pinned development lock with the repository's Linux policy."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from argparse import ArgumentParser
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__:
    from .dependency_pins import read_dependency_pins
else:
    from dependency_pins import read_dependency_pins

CANONICAL_PLATFORM = "linux"
CANONICAL_PYTHON = (3, 12)
ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "requirements" / "dev.in"
OUTPUT = ROOT / "requirements" / "dev.txt"
PIP_TOOLS_VERSION = read_dependency_pins(INPUT, ("pip-tools",))["pip-tools"]
COMPILE_FLAGS = (
    "--no-config",
    "--generate-hashes",
    "--resolver=backtracking",
    "--strip-extras",
    "--allow-unsafe",
    "--no-upgrade",
    "--reuse-hashes",
    "--build-isolation",
    "--newline=lf",
    "--header",
    "--no-color",
    "--annotate",
    "--annotation-style=line",
    "--no-emit-index-url",
    "--no-emit-trusted-host",
    "--no-emit-find-links",
    f"--output-file={OUTPUT.relative_to(ROOT)}",
    str(INPUT.relative_to(ROOT)),
)
FRESH_COMPILE_FLAGS = tuple(flag for flag in COMPILE_FLAGS if flag != "--reuse-hashes")
PINNED_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\;]+)")
NAME_SEPARATOR = re.compile(r"[-_.]+")


def is_canonical_environment(
    platform: str,
    python_version: tuple[int, int],
    github_actions: str | None,
    image_os: str | None,
) -> bool:
    return (
        platform == CANONICAL_PLATFORM
        and python_version == CANONICAL_PYTHON
        and github_actions == "true"
        and image_os == "ubuntu24"
    )


def canonical_package_name(name: str) -> str:
    """Return the comparison form used for Python distribution names."""
    return NAME_SEPARATOR.sub("-", name).lower()


def direct_requirement_names(path: Path) -> frozenset[str]:
    """Return canonical names of every exact direct input pin."""
    return frozenset(
        canonical_package_name(match.group(1))
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := PINNED_REQUIREMENT.match(line))
    )


def write_version_constraints(
    lock: Path, destination: Path, *, excluded_names: frozenset[str] = frozenset()
) -> None:
    """Preserve locked versions except direct inputs that may have changed."""
    constraints = [
        f"{match.group(1)}=={match.group(2)}"
        for line in lock.read_text(encoding="utf-8").splitlines()
        if (match := PINNED_REQUIREMENT.match(line))
        and canonical_package_name(match.group(1)) not in excluded_names
    ]
    destination.write_text("\n".join(constraints) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="remove the existing output and regenerate without reusing its hashes",
    )
    arguments = parser.parse_args()
    if not is_canonical_environment(
        sys.platform,
        sys.version_info[:2],
        os.environ.get("GITHUB_ACTIONS"),
        os.environ.get("ImageOS"),
    ):
        print(
            "development locks must be generated in the canonical GitHub "
            "ubuntu-24.04/Python 3.12 environment",
            file=sys.stderr,
        )
        return 2

    installed = version("pip-tools")
    if installed != PIP_TOOLS_VERSION:
        print(
            f"pip-tools {PIP_TOOLS_VERSION} is required; found {installed}",
            file=sys.stderr,
        )
        return 2

    environment = os.environ.copy()
    environment["CUSTOM_COMPILE_COMMAND"] = "python tools/compile_dev_lock.py"
    if not arguments.fresh:
        return subprocess.run(
            [sys.executable, "-m", "piptools", "compile", *COMPILE_FLAGS],
            cwd=ROOT,
            env=environment,
            check=False,
        ).returncode

    if not OUTPUT.exists():
        print(f"cannot preserve versions: {OUTPUT} does not exist", file=sys.stderr)
        return 2
    with TemporaryDirectory() as temporary_directory:
        constraints = Path(temporary_directory) / "dev-versions.txt"
        write_version_constraints(
            OUTPUT,
            constraints,
            excluded_names=direct_requirement_names(INPUT),
        )
        OUTPUT.unlink()
        flags = (*FRESH_COMPILE_FLAGS, "--constraint", str(constraints))
        result = subprocess.run(
            [sys.executable, "-m", "piptools", "compile", *flags],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        if result.returncode == 0:
            generated = OUTPUT.read_text(encoding="utf-8")
            generated = generated.replace(f"-c {constraints}, ", "")
            OUTPUT.write_text(generated, encoding="utf-8", newline="\n")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
