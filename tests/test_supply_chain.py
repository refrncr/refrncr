import json
import re
import tomllib
from datetime import date
from pathlib import Path

import pytest
import yaml
from packaging.version import Version

from tools.compile_dev_lock import (
    COMPILE_FLAGS as DEV_COMPILE_FLAGS,
)
from tools.compile_dev_lock import (
    FRESH_COMPILE_FLAGS as DEV_FRESH_COMPILE_FLAGS,
)
from tools.compile_dev_lock import (
    PIP_TOOLS_VERSION as DEV_PIP_TOOLS_VERSION,
)
from tools.compile_dev_lock import write_version_constraints
from tools.compile_release_lock import (
    CANONICAL_PLATFORM,
    CANONICAL_PYTHON,
    COMPILE_FLAGS,
    PIP_TOOLS_VERSION,
    is_canonical_environment,
)
from tools.normalize_sdist import normalize_sdist
from tools.release_publication import publication_required, sha256
from tools.release_upgrade import preceding_public_version, wheel_identity
from tools.supply_chain import gate_audit, gate_sarif, load_exceptions, validate_action_pins

ROOT = Path(__file__).resolve().parents[1]


def exception(**overrides):
    value = {
        "advisory_id": "GHSA-test-safe-fixture",
        "package": "fixture-package",
        "version": "1.2.3",
        "risk_assessment": "Synthetic test finding has no production exposure.",
        "mitigation": "The fixture cannot enter the runtime graph.",
        "owner": "Security maintainer",
        "approver": "Release maintainer",
        "created_on": "2026-09-01",
        "expires_on": "2026-09-30",
        "remediation_plan": "Remove the fixture before expiry.",
    }
    value.update(overrides)
    return value


def write_register(tmp_path, entries):
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"schema_version": 1, "exceptions": entries}))
    return path


def audit_report(package="fixture-package", version="1.2.3", advisory="GHSA-test-safe-fixture"):
    return {"dependencies": [{"name": package, "version": version, "vulns": [{"id": advisory}]}]}


def test_release_input_matches_public_runtime_ranges():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = set(metadata["project"]["dependencies"])
    release_input = {
        line
        for line in (ROOT / "requirements/release.in").read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert release_input == declared
    assert all("==" not in requirement for requirement in declared)


def test_release_lock_is_hashed_and_fully_pinned():
    lock = (ROOT / "requirements/release.txt").read_text()
    assert "--hash=sha256:" in lock
    requirements = [
        line for line in lock.splitlines() if line and not line.startswith(("#", " ", "--"))
    ]
    assert requirements
    assert all("==" in line and line.endswith("\\") for line in requirements)
    assert lock.count("--hash=sha256:") >= len(requirements)
    assert "#    python tools/compile_release_lock.py" in lock


def test_release_lock_generator_pins_tool_and_all_relevant_flags():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev = metadata["project"]["optional-dependencies"]["dev"]
    assert f"pip-tools=={PIP_TOOLS_VERSION}" in dev
    assert set(COMPILE_FLAGS) >= {
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
    }
    security = (ROOT / ".github/workflows/security.yml").read_text()
    assert "python tools/compile_release_lock.py" in security
    assert "python -m piptools compile" not in security
    workflow = load_workflow("security.yml")
    dependency_job = workflow["jobs"]["dependency-and-policy"]
    assert dependency_job["runs-on"] == "ubuntu-24.04"
    assert CANONICAL_PLATFORM == "linux"
    assert CANONICAL_PYTHON == (3, 12)
    assert is_canonical_environment("linux", (3, 12), "true", "ubuntu24")
    assert not is_canonical_environment("darwin", (3, 12), "true", "ubuntu24")
    assert not is_canonical_environment("linux", (3, 13), "true", "ubuntu24")
    assert not is_canonical_environment("linux", (3, 12), None, None)
    setup_python = next(
        step for step in dependency_job["steps"] if "setup-python@" in step.get("uses", "")
    )
    assert setup_python["with"]["python-version"] == "3.12"


def test_development_tool_pins_match_project_hooks_and_workflows():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev = metadata["project"]["optional-dependencies"]["dev"]
    dev_input = parse_pins(ROOT / "requirements/dev.in")
    dev_lock = parse_pins(ROOT / "requirements/dev.txt")
    pre_commit = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    release = load_workflow("release.yml")

    assert DEV_PIP_TOOLS_VERSION == dev_input["pip-tools"]
    assert PIP_TOOLS_VERSION == dev_input["pip-tools"]

    for package, pinned in dev_input.items():
        assert f"{package}=={pinned}" in dev
        assert dev_lock[package] == pinned

    ruff_hook = next(repo for repo in pre_commit["repos"] if "ruff-pre-commit" in repo["repo"])
    assert ruff_hook["rev"] == f"v{dev_input['ruff']}"
    release_install = "\n".join(
        step["run"]
        for step in release["jobs"]["build"]["steps"]
        if "pip install" in step.get("run", "")
    )
    release_pins = dict(re.findall(r"([A-Za-z0-9_.-]+)==([0-9][^\s]+)", release_install))
    for package in ("build", "cyclonedx-bom", "twine"):
        assert release_pins[package] == dev_input[package]


def test_development_lock_generator_is_linux_pinned_and_checked_in_ci():
    assert (
        f"pip-tools=={DEV_PIP_TOOLS_VERSION}"
        in (
            tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
                "optional-dependencies"
            ]["dev"]
        )
    )
    assert set(DEV_COMPILE_FLAGS) >= {
        "--no-config",
        "--generate-hashes",
        "--resolver=backtracking",
        "--strip-extras",
        "--allow-unsafe",
        "--build-isolation",
        "--no-upgrade",
        "--reuse-hashes",
    }
    assert "--reuse-hashes" not in DEV_FRESH_COMPILE_FLAGS
    security = (ROOT / ".github/workflows/security.yml").read_text()
    assert "python tools/compile_dev_lock.py --fresh" in security
    assert "git diff --exit-code -- requirements/dev.txt" in security


def test_fresh_development_lock_constraints_preserve_versions_without_hashes(tmp_path):
    lock = tmp_path / "dev.txt"
    lock.write_text("# generated\n\nalpha==1.2.3\n--hash=sha256:old\nbeta==4.5.6\n")
    constraints = tmp_path / "constraints.txt"

    write_version_constraints(lock, constraints)

    assert constraints.read_text() == "alpha==1.2.3\nbeta==4.5.6\n"


def parse_pins(path):
    pins = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s]+)", line)
        if match:
            pins[match.group(1)] = match.group(2)
    return pins


def test_exception_register_accepts_valid_exception(tmp_path):
    assert load_exceptions(write_register(tmp_path, [exception()]), today=date(2026, 9, 20))


@pytest.mark.parametrize(
    "change, message",
    [
        ({"owner": None}, "empty or non-string"),
        ({"expires_on": "2026-08-31"}, "expires before"),
        ({"expires_on": "2026-10-02"}, "longer than 30"),
    ],
)
def test_exception_register_rejects_invalid_entries(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        load_exceptions(write_register(tmp_path, [exception(**change)]), today=date(2026, 9, 20))


def test_exception_register_rejects_missing_field(tmp_path):
    entry = exception()
    del entry["approver"]
    with pytest.raises(ValueError, match="missing fields: approver"):
        load_exceptions(write_register(tmp_path, [entry]), today=date(2026, 9, 20))


def test_exception_register_rejects_expired_entry(tmp_path):
    with pytest.raises(ValueError, match="expired"):
        load_exceptions(
            write_register(tmp_path, [exception(expires_on="2026-09-19")]), today=date(2026, 9, 20)
        )


def test_audit_gate_requires_exact_advisory_package_and_version_match():
    finding = audit_report()
    assert gate_audit(finding, [exception()]) == []
    for mismatch in ({"package": "other"}, {"version": "1.2.4"}, {"advisory_id": "GHSA-other"}):
        assert gate_audit(finding, [exception(**mismatch)])
    assert gate_audit(finding, [])


def test_sarif_gate_blocks_actionable_results_only():
    assert gate_sarif({"runs": [{"results": [{"ruleId": "safe", "level": "note"}]}]}) == []
    assert gate_sarif({"runs": [{"results": [{"ruleId": "unsafe", "level": "error"}]}]}) == [
        "unsafe"
    ]


def test_all_external_actions_use_full_commit_shas():
    assert validate_action_pins(ROOT) == []


def test_action_pin_validator_rejects_tags_branches_and_short_shas(tmp_path):
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    for index, reference in enumerate(("v4", "main", "abc1234")):
        (workflows / f"bad-{index}.yml").write_text(
            f"steps:\n  - uses: actions/checkout@{reference}\n"
        )
    assert len(validate_action_pins(tmp_path)) == 3


def load_workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_dependabot_covers_python_and_actions():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert {item["package-ecosystem"] for item in config["updates"]} >= {"pip", "github-actions"}


def test_dependabot_lock_refresh_has_two_job_security_boundary():
    workflow = load_workflow("dependabot-lock-refresh.yml")
    text = (ROOT / ".github/workflows/dependabot-lock-refresh.yml").read_text()
    generate = workflow["jobs"]["generate"]
    push = workflow["jobs"]["push"]

    assert set(workflow["jobs"]) == {"generate", "push"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "true"
    assert generate["timeout-minutes"] == "20"
    assert push["timeout-minutes"] == "10"
    assert push["needs"] == "generate"
    assert "pull_request_target" not in text

    for job in (generate, push):
        checkout = next(
            step for step in job.get("steps", []) if "actions/checkout@" in step.get("uses", "")
        )
        assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
        assert checkout["with"]["persist-credentials"] == "false"
        assert any("actions/setup-python@" in step.get("uses", "") for step in job.get("steps", []))
        for step in job.get("steps", []):
            assert "${{ github." not in step.get("run", "")

    push_text = "\n".join(step.get("run", "") for step in push.get("steps", []))
    assert "python -E -s validate_dependency_refresh.py preflight" in push_text
    assert "python -E -s validate_dependency_refresh.py apply" in push_text
    assert push_text.index("preflight") < push_text.index("validate_dependency_refresh.py apply")
    assert push_text.index("validate_dependency_refresh.py apply") < push_text.index("worktree")
    assert 'cd "$TRUSTED_DIR"' in push_text
    assert "--binary" not in text
    assert "--force" not in text and "--force-with-lease" not in text
    assert "core.hooksPath=/dev/null" in push_text
    assert "git add ." not in push_text
    assert "tools/validate_dependency_refresh.py" in push_text
    assert "tools/sync_dependency_pins.py" in push_text
    assert "tools/dependency_pins.py" in push_text
    validator = (ROOT / "tools/validate_dependency_refresh.py").read_text(encoding="utf-8")
    assert 'f"--include={path}" for path in sorted(ALLOWED_PATHS)' in validator


def test_dependabot_lock_refresh_limits_secret_exposure_to_trusted_job():
    workflow = load_workflow("dependabot-lock-refresh.yml")
    generate = workflow["jobs"]["generate"]
    push = workflow["jobs"]["push"]
    generate_text = repr(generate)
    push_steps = push.get("steps", [])

    assert "secrets." not in generate_text
    secret_check = next(
        step for step in push_steps if step.get("name") == "Require configured Dependabot secrets"
    )
    assert secret_check["env"] == {
        "HAS_CLIENT_ID": "${{ secrets.LOCK_REFRESH_CLIENT_ID != '' }}",
        "HAS_PRIVATE_KEY": "${{ secrets.LOCK_REFRESH_PRIVATE_KEY != '' }}",
        "TRUSTED_DIR": "${{ runner.temp }}/trusted-refresh",
    }
    token = next(step for step in push_steps if "create-github-app-token@" in step.get("uses", ""))
    assert token["with"]["client-id"] == "${{ secrets.LOCK_REFRESH_CLIENT_ID }}"
    assert token["with"]["private-key"] == "${{ secrets.LOCK_REFRESH_PRIVATE_KEY }}"
    assert token["with"]["permission-contents"] == "write"
    assert token["with"]["permission-workflows"] == "write"
    assert "owner" not in token["with"] and "repositories" not in token["with"]


def test_pr_workflows_are_read_only_and_release_permissions_are_job_scoped():
    for name in ("ci.yml", "security.yml"):
        workflow = load_workflow(name)
        assert workflow.get("permissions") == {"contents": "read"}
    codeql = load_workflow("codeql.yml")
    assert codeql.get("permissions") == {"contents": "read"}
    release = load_workflow("release.yml")
    assert release.get("permissions") == {"contents": "read"}
    privileged = release["jobs"]["publish"]["permissions"]
    assert privileged == {"contents": "write", "id-token": "write", "attestations": "write"}
    assert release["jobs"]["build"]["permissions"] == {"contents": "read", "checks": "read"}


def test_security_workflow_has_required_events_and_safe_gitleaks_redaction():
    text = (ROOT / ".github/workflows/security.yml").read_text()
    workflow = load_workflow("security.yml")
    setup_go_pins = [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/setup-go@")
    ]
    assert "pull_request:" in text and "push:" in text and "schedule:" in text
    assert "fetch-depth: 0" in text
    assert len(setup_go_pins) == 2
    assert len(set(setup_go_pins)) == 1
    assert text.count('go-version: "1.24.11"') == 2
    assert text.count("go install github.com/zricethezav/gitleaks/v8@v8.30.1") == 2
    assert "go install github.com/gitleaks/gitleaks/v8" not in text
    assert "gitleaks/gitleaks-action@" not in text
    assert "GITLEAKS_LICENSE" not in text
    assert 'gitleaks" git . --log-opts="--all"' in text
    assert "--redact=100" in text
    assert 'test "$status" -eq 1' in text
    assert '! grep -Fq "$(cat synthetic-secret.txt)"' in text
    assert "leaks found: 1" not in text
    gitleaks = (ROOT / ".gitleaks.toml").read_text()
    assert gitleaks.count("redact = 100") == 1
    assert gitleaks.count("[[allowlists]]") == 2
    assert "^src/refrncr/(dataset_versions\\.py|versions/concepts\\.py)$" in gitleaks
    assert "^docs/development/(ref-51-report|supply-chain)\\.md$" in gitleaks
    assert "key,\\s+allow_unpublished=True" in gitleaks
    assert gitleaks.count('condition = "AND"') == 2


def test_codeql_analyzes_python_uploads_and_has_a_real_sarif_gate():
    workflow = load_workflow("codeql.yml")
    text = (ROOT / ".github/workflows/codeql.yml").read_text()
    assert "languages: python" in text
    pull_request = workflow["jobs"]["analyze-pull-request"]
    trusted = workflow["jobs"]["analyze-and-gate"]
    assert workflow["permissions"] == {"contents": "read"}
    assert pull_request["permissions"] == {"actions": "read", "contents": "read"}
    assert trusted["permissions"] == {"actions": "read", "contents": "read"}
    assert text.count("upload: never") == 2
    assert "upload: false" not in text
    assert all(
        any("gate-sarif" in step.get("run", "") for step in job["steps"])
        for job in (pull_request, trusted)
    )
    assert all(
        "upload-sarif@" not in step.get("uses", "")
        for job in (pull_request, trusted)
        for step in job["steps"]
    )


def test_release_attests_exact_built_wheel_and_sdist_and_generates_sbom():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert "cyclonedx-py requirements requirements/release.txt" in text
    assert "subject-path: |" in text
    assert "release-artifacts/distributions/*.whl" in text
    assert "release-artifacts/distributions/*.tar.gz" in text
    assert "dist/**" not in text
    assert re.search(r"python -m build --outdir release-artifacts/distributions", text)
    assert 'gh release upload "$tag" --repo "$repository"' in text
    assert 'repository="${{ github.repository }}"' in text
    assert "python -m pipx install release-artifacts/distributions/*.whl" in text
    assert "python tools/release_smoke.py http://127.0.0.1:8765" in text
    assert "python tools/release_upgrade.py" in text
    assert "python tools/normalize_sdist.py" in text
    assert "sha256sum --check SHA256SUMS" in text
    assert "Existing release asset differs" in text
    assert "cp tools/release_publication.py release-artifacts/release_publication.py" in text
    assert "python release-artifacts/release_publication.py" in text
    assert "if: steps.pypi.outputs.publish == 'true'" in text


def write_test_wheel(path, *, version="0.1.0rc2"):
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"refrncr-{version}.dist-info/METADATA",
            f"Name: refrncr\nVersion: {version}\n",
        )


def test_release_publication_requires_absent_version_and_accepts_identical_replay(
    tmp_path, monkeypatch
):
    directory = tmp_path / "dist"
    directory.mkdir()
    wheel = directory / "refrncr-0.1.0rc2-py3-none-any.whl"
    sdist = directory / "refrncr-0.1.0rc2.tar.gz"
    write_test_wheel(wheel)
    sdist.write_bytes(b"source distribution")

    monkeypatch.setattr("tools.release_publication.published_hashes", lambda *_args: None)
    assert publication_required(directory)

    hashes = {wheel.name: sha256(wheel), sdist.name: sha256(sdist)}
    monkeypatch.setattr("tools.release_publication.published_hashes", lambda *_args: hashes)
    assert not publication_required(directory)


def test_release_publication_rejects_partial_or_different_existing_version(tmp_path, monkeypatch):
    directory = tmp_path / "dist"
    directory.mkdir()
    wheel = directory / "refrncr-0.1.0rc2-py3-none-any.whl"
    sdist = directory / "refrncr-0.1.0rc2.tar.gz"
    write_test_wheel(wheel)
    sdist.write_bytes(b"source distribution")
    monkeypatch.setattr(
        "tools.release_publication.published_hashes",
        lambda *_args: {wheel.name: "0" * 64},
    )

    with pytest.raises(RuntimeError, match="partial or different publication"):
        publication_required(directory)


def test_stable_upgrade_selects_immediately_preceding_public_release(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {
                    "releases": {
                        "0.1.0rc1": [],
                        "0.1.0rc2": [{"filename": "published"}],
                        "0.1.0": [{"filename": "current"}],
                        "0.2.0": [{"filename": "future"}],
                    }
                }
            ).encode()

    monkeypatch.setattr(
        "tools.release_upgrade.urllib.request.urlopen", lambda *_a, **_k: Response()
    )
    assert str(preceding_public_version("refrncr", Version("0.1.0"))) == "0.1.0rc2"


def test_upgrade_wheel_identity_reads_pep_440_version(tmp_path):
    wheel = tmp_path / "refrncr-0.1.0-py3-none-any.whl"
    write_test_wheel(wheel, version="0.1.0")

    name, version = wheel_identity(wheel)

    assert name == "refrncr"
    assert str(version) == "0.1.0"


def test_sdist_normalization_is_reproducible(tmp_path):
    import tarfile

    archives = []
    for index in range(2):
        archive = tmp_path / f"distribution-{index}.tar.gz"
        payload = tmp_path / f"payload-{index}.txt"
        payload.write_text("same release content")
        with tarfile.open(archive, "w:gz") as target:
            target.add(payload, arcname="refrncr-1.0/payload.txt")
        normalize_sdist(archive, 1_700_000_000)
        archives.append(archive)

    assert archives[0].read_bytes() == archives[1].read_bytes()


def test_release_requires_the_actual_main_quality_checks():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    match = re.search(r"for check in \\\n(?P<checks>.*?)\n          do", text, re.DOTALL)
    assert match is not None
    configured = {
        line.strip().removesuffix(" \\")
        for line in match.group("checks").splitlines()
        if line.strip()
    }
    assert configured == {
        "installed-runtime",
        "browser-core",
        "test-sqlite",
        "test-sqlite-slow",
        "test-postgres",
        "test-postgres-slow",
        "coverage",
        "dependency-and-policy",
        "gitleaks-safe-fixture",
        "analyze-and-gate",
    }
    assert '.name == "test"' not in text
