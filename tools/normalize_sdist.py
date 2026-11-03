"""Normalize a source distribution so retries produce identical bytes."""

from __future__ import annotations

import copy
import gzip
import io
import os
import sys
import tarfile
from pathlib import Path


def normalize_sdist(path: Path, epoch: int) -> None:
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, "r:gz") as source:
        for member in source.getmembers():
            extracted = source.extractfile(member) if member.isfile() else None
            entries.append((member, extracted.read() if extracted is not None else None))

    temporary = path.with_suffix(path.suffix + ".normalized")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as target:
                for original, content in sorted(entries, key=lambda item: item[0].name):
                    member = copy.copy(original)
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    member.mtime = epoch
                    member.pax_headers = {}
                    target.addfile(member, io.BytesIO(content) if content is not None else None)
    os.replace(temporary, path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: normalize_sdist.py SDIST SOURCE_DATE_EPOCH")
    normalize_sdist(Path(sys.argv[1]), int(sys.argv[2]))
