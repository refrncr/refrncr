"""Decide whether exact release distributions still need publication to PyPI."""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wheel_identity(wheel: Path) -> tuple[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ValueError(f"expected one wheel METADATA file, found {len(metadata_names)}")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise ValueError("wheel metadata must contain Name and Version")
    return name, version


def published_hashes(name: str, version: str) -> dict[str, str] | None:
    encoded_name = urllib.parse.quote(name, safe="")
    encoded_version = urllib.parse.quote(version, safe="")
    url = f"https://pypi.org/pypi/{encoded_name}/{encoded_version}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    return {item["filename"]: item["digests"]["sha256"] for item in payload["urls"]}


def publication_required(distribution_directory: Path) -> bool:
    distributions = sorted(path for path in distribution_directory.iterdir() if path.is_file())
    wheels = [path for path in distributions if path.suffix == ".whl"]
    sdists = [path for path in distributions if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1 or len(distributions) != 2:
        raise ValueError("expected exactly one wheel and one .tar.gz source distribution")
    name, version = wheel_identity(wheels[0])
    remote = published_hashes(name, version)
    if remote is None:
        return True
    local = {path.name: sha256(path) for path in distributions}
    if remote != local:
        raise RuntimeError(
            f"PyPI already contains a partial or different publication for {name} {version}"
        )
    return False


def main(distribution_directory: Path, github_output: Path) -> None:
    publish = publication_required(distribution_directory)
    with github_output.open("a", encoding="utf-8") as output:
        output.write(f"publish={'true' if publish else 'false'}\n")
    if not publish:
        print("PyPI already contains the identical wheel and source distribution; skipping upload")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: release_publication.py DISTRIBUTION_DIRECTORY GITHUB_OUTPUT")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
