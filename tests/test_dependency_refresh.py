import shutil
import subprocess
from pathlib import Path

import pytest

from tools.validate_dependency_refresh import (
    ALLOWED_PATHS,
    RefreshValidationError,
    prepare_patch,
    require_secret_flags,
    validate_patch,
    validate_worktree,
)

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def initialize_repository(root: Path) -> str:
    for relative in ALLOWED_PATHS | {"requirements/dev.in"}:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    return git(root, "rev-parse", "HEAD")


def write_patch(path: Path, metadata: str, old_path: str = "requirements/dev.txt") -> None:
    path.write_text(
        f"diff --git a/{old_path} b/{old_path}\n"
        f"{metadata}"
        f"--- a/{old_path}\n"
        f"+++ b/{old_path}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )


def test_prepare_patch_records_changed_decision(tmp_path):
    initialize_repository(tmp_path)
    patch = tmp_path / "refresh.patch"
    output = tmp_path / "output"

    assert not prepare_patch(tmp_path, patch, output)
    assert output.read_text(encoding="utf-8") == "changed=false\n"

    (tmp_path / "requirements/dev.txt").write_text("changed\n", encoding="utf-8")
    assert prepare_patch(tmp_path, patch, output)
    assert "--binary" not in patch.read_text(encoding="utf-8")
    assert output.read_text(encoding="utf-8").endswith("changed=true\n")


def test_patch_preflight_accepts_an_allowlisted_text_modification(tmp_path):
    patch = tmp_path / "refresh.patch"
    write_patch(patch, "index 1234567..abcdef0 100644\n")

    assert validate_patch(patch) == {"requirements/dev.txt"}


@pytest.mark.parametrize(
    ("patch_text", "message"),
    [
        (
            "diff --git a/tools/unsafe.py b/tools/unsafe.py\n"
            "index 1234567..abcdef0 100644\n"
            "--- a/tools/unsafe.py\n+++ b/tools/unsafe.py\n@@ -1 +1 @@\n-old\n+new\n",
            "not allowlisted",
        ),
        (
            "diff --git a/requirements/dev.txt b/requirements/dev.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n+++ b/requirements/dev.txt\n@@ -0,0 +1 @@\n+new\n",
            "unsupported patch metadata",
        ),
        (
            "diff --git a/requirements/dev.txt b/requirements/dev.txt\n"
            "deleted file mode 100644\n"
            "--- a/requirements/dev.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
            "unsupported patch metadata",
        ),
        (
            "diff --git a/requirements/dev.txt b/requirements/release.txt\n"
            "similarity index 100%\nrename from requirements/dev.txt\n"
            "rename to requirements/release.txt\n",
            "renamed or unsafe",
        ),
        (
            "diff --git a/requirements/dev.txt b/requirements/dev.txt\n"
            "old mode 100644\nnew mode 100755\n",
            "unsupported patch metadata",
        ),
        (
            "diff --git a/requirements/dev.txt b/requirements/dev.txt\n"
            "old mode 100644\nnew mode 120000\n",
            "unsupported patch metadata",
        ),
        (
            "diff --git a/requirements/dev.txt b/requirements/dev.txt\n"
            "index 1234567..abcdef0 100644\nGIT binary patch\nliteral 1\nAcmZQz\n",
            "unsupported patch metadata",
        ),
    ],
)
def test_patch_preflight_rejects_unsafe_metadata(tmp_path, patch_text, message):
    patch = tmp_path / "refresh.patch"
    patch.write_text(patch_text, encoding="utf-8")

    with pytest.raises(RefreshValidationError, match=message):
        validate_patch(patch)


def test_worktree_validation_accepts_only_reproducible_allowlisted_changes(tmp_path):
    head = initialize_repository(tmp_path)
    dev_lock = tmp_path / "requirements/dev.txt"
    dev_lock.write_text(dev_lock.read_text(encoding="utf-8") + "# refreshed\n", encoding="utf-8")

    assert validate_worktree(tmp_path, head) == {"requirements/dev.txt"}


def test_worktree_validation_rejects_unmanaged_derived_change(tmp_path):
    head = initialize_repository(tmp_path)
    release = tmp_path / ".github/workflows/release.yml"
    release.write_text(release.read_text(encoding="utf-8") + "# injected\n", encoding="utf-8")

    with pytest.raises(RefreshValidationError, match="derived file has non-allowlisted changes"):
        validate_worktree(tmp_path, head)


def test_secret_check_receives_boolean_availability_only():
    require_secret_flags("true", "true")
    with pytest.raises(RefreshValidationError, match="LOCK_REFRESH_PRIVATE_KEY"):
        require_secret_flags("true", "false")
