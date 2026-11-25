import hashlib
from pathlib import Path

import pytest

from tools.release_bundle import (
    ReleaseBundleError,
    require_exact_distributions,
    verify_bundle_inventory,
)


def write_manifest(root: Path, relative_paths: list[str]) -> Path:
    lines = []
    for relative in relative_paths:
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}\n")
    manifest = root / "BUNDLE_SHA256SUMS"
    manifest.write_text("".join(lines), encoding="utf-8")
    return manifest


def exact_bundle(root: Path) -> tuple[Path, list[str]]:
    (root / "distributions").mkdir()
    relative_paths = [
        "SHA256SUMS",
        "refrncr.cdx.json",
        "release_bundle.py",
        "release_publication.py",
        "distributions/refrncr-0.1.0rc5-py3-none-any.whl",
        "distributions/refrncr-0.1.0rc5.tar.gz",
    ]
    for relative in relative_paths:
        (root / relative).write_text(f"fixture for {relative}\n", encoding="utf-8")
    return write_manifest(root, relative_paths), relative_paths


def test_bundle_inventory_accepts_exact_recursive_file_set(tmp_path):
    manifest, _ = exact_bundle(tmp_path)
    verify_bundle_inventory(tmp_path, manifest)


@pytest.mark.parametrize("extra", ["distributions/evil.whl", ".hidden", "nested/.hidden"])
def test_bundle_inventory_rejects_extra_files(tmp_path, extra):
    manifest, _ = exact_bundle(tmp_path)
    path = tmp_path / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("injected\n", encoding="utf-8")

    with pytest.raises(ReleaseBundleError, match="extra"):
        verify_bundle_inventory(tmp_path, manifest)


def test_bundle_inventory_rejects_missing_file(tmp_path):
    manifest, relative_paths = exact_bundle(tmp_path)
    (tmp_path / relative_paths[-1]).unlink()

    with pytest.raises(ReleaseBundleError, match="missing"):
        verify_bundle_inventory(tmp_path, manifest)


def test_bundle_inventory_rejects_symlink(tmp_path):
    manifest, _ = exact_bundle(tmp_path)
    (tmp_path / "injected-link").symlink_to("refrncr.cdx.json")

    with pytest.raises(ReleaseBundleError, match="symlinks"):
        verify_bundle_inventory(tmp_path, manifest)


def test_distribution_directory_requires_exactly_one_wheel_and_sdist(tmp_path):
    wheel = tmp_path / "refrncr-0.1.0rc5-py3-none-any.whl"
    sdist = tmp_path / "refrncr-0.1.0rc5.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    require_exact_distributions(tmp_path)

    (tmp_path / "extra.whl").write_bytes(b"injected")
    with pytest.raises(ReleaseBundleError, match="exactly one"):
        require_exact_distributions(tmp_path)
