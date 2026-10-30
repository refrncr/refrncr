"""Verify the built wheel carries refrncr's declared license and exact license text."""

from __future__ import annotations

import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path


def verify(wheel_path: Path, source_license_path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(wheel_path) as wheel:
        metadata_paths = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError(f"Expected one wheel METADATA file, found {len(metadata_paths)}")

        metadata = BytesParser().parsebytes(wheel.read(metadata_paths[0]))
        if metadata["License-Expression"] != "Elastic-2.0":
            raise ValueError("Wheel License-Expression is not Elastic-2.0")
        if metadata.get_all("License-File") != ["LICENSE"]:
            raise ValueError("Wheel License-File metadata does not name LICENSE exactly once")

        license_path = metadata_paths[0].removesuffix("METADATA") + "licenses/LICENSE"
        if license_path not in wheel.namelist():
            raise ValueError(f"Wheel does not contain {license_path}")
        if wheel.read(license_path) != source_license_path.read_bytes():
            raise ValueError("Wheel license text differs from the repository LICENSE")

    return (
        f"License-Expression: {metadata['License-Expression']}",
        f"License-File: {metadata['License-File']}",
        f"License-Archive-Path: {license_path}",
    )


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_distribution_license.py WHEEL LICENSE")
    for line in verify(Path(sys.argv[1]), Path(sys.argv[2])):
        print(line)


if __name__ == "__main__":
    main()
