"""Generate the release lock with the repository's canonical pip-tools policy."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

PIP_TOOLS_VERSION = "7.6.1"
CANONICAL_PLATFORM = "linux"
CANONICAL_PYTHON = (3, 12)
ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "requirements" / "release.in"
OUTPUT = ROOT / "requirements" / "release.txt"
COMPILE_FLAGS = (
    "--no-config",
    "--generate-hashes",
    "--resolver=backtracking",
    "--strip-extras",
    "--no-allow-unsafe",
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


def main() -> int:
    if not is_canonical_environment(
        sys.platform,
        sys.version_info[:2],
        os.environ.get("GITHUB_ACTIONS"),
        os.environ.get("ImageOS"),
    ):
        print(
            "release locks must be generated in the canonical GitHub "
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
    environment["CUSTOM_COMPILE_COMMAND"] = "python tools/compile_release_lock.py"
    environment.setdefault(
        "PIP_TOOLS_CACHE_DIR", str(Path(tempfile.gettempdir()) / "refrncr-pip-tools-cache")
    )
    return subprocess.run(
        [sys.executable, "-m", "piptools", "compile", *COMPILE_FLAGS],
        cwd=ROOT,
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
