import hashlib
import shutil
import subprocess
import sys
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


def test_isolated_validator_rejects_planted_standard_library_module(tmp_path):
    source = Path(__file__).parents[1] / "tools/release_bundle.py"
    shutil.copyfile(source, tmp_path / "release_bundle.py")
    (tmp_path / "release_publication.py").write_text("fixture\n", encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text("fixture\n", encoding="utf-8")
    (tmp_path / "refrncr.cdx.json").write_text("fixture\n", encoding="utf-8")
    distributions = tmp_path / "distributions"
    distributions.mkdir()
    (distributions / "refrncr-0.1.0rc5-py3-none-any.whl").write_bytes(b"wheel")
    (distributions / "refrncr-0.1.0rc5.tar.gz").write_bytes(b"sdist")
    bundled = [
        "SHA256SUMS",
        "refrncr.cdx.json",
        "release_bundle.py",
        "release_publication.py",
        "distributions/refrncr-0.1.0rc5-py3-none-any.whl",
        "distributions/refrncr-0.1.0rc5.tar.gz",
    ]
    manifest = write_manifest(tmp_path, bundled)
    marker = tmp_path / "planted-code-ran"
    (tmp_path / "argparse.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(tmp_path / "release_bundle.py"),
            "inventory",
            str(tmp_path),
            str(manifest),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "extra: argparse.py" in result.stderr
    assert not marker.exists()


def test_distribution_directory_requires_exactly_one_wheel_and_sdist(tmp_path):
    wheel = tmp_path / "refrncr-0.1.0rc5-py3-none-any.whl"
    sdist = tmp_path / "refrncr-0.1.0rc5.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    require_exact_distributions(tmp_path)

    (tmp_path / "extra.whl").write_bytes(b"injected")
    with pytest.raises(ReleaseBundleError, match="exactly one"):
        require_exact_distributions(tmp_path)
