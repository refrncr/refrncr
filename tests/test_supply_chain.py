import json
import re
import tomllib
from datetime import date
from pathlib import Path

import pytest
import yaml

from tools.compile_dev_lock import (
    COMPILE_FLAGS as DEV_COMPILE_FLAGS,
)
from tools.compile_dev_lock import (
    PIP_TOOLS_VERSION as DEV_PIP_TOOLS_VERSION,
)
from tools.compile_release_lock import (
    CANONICAL_PLATFORM,
    CANONICAL_PYTHON,
    COMPILE_FLAGS,
    PIP_TOOLS_VERSION,
    is_canonical_environment,
)
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
    assert "greenlet==" in lock


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
    dev_input = (ROOT / "requirements/dev.in").read_text()
    dev_lock = (ROOT / "requirements/dev.txt").read_text()
    pre_commit = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    release = load_workflow("release.yml")

    expected = {
        "ruff": "0.16.5",
        "pytest": "8.4.2",
        "pytest-cov": "6.2.1",
        "cyclonedx-bom": "7.4.0",
    }
    for package, pinned in expected.items():
        assert f"{package}=={pinned}" in dev
        assert f"{package}=={pinned}" in dev_input
        assert f"{package}=={pinned}" in dev_lock

    ruff_hook = next(repo for repo in pre_commit["repos"] if "ruff-pre-commit" in repo["repo"])
    assert ruff_hook["rev"] == "v0.16.5"
    release_install = next(
        step["run"]
        for step in release["jobs"]["build"]["steps"]
        if "cyclonedx-bom==" in step.get("run", "")
    )
    assert "cyclonedx-bom==7.4.0" in release_install


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
    security = (ROOT / ".github/workflows/security.yml").read_text()
    assert "python tools/compile_dev_lock.py" in security
    assert "git diff --exit-code -- requirements/dev.txt" in security


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
    assert "pull_request:" in text and "push:" in text and "schedule:" in text
    assert "fetch-depth: 0" in text
    assert text.count("actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e") == 2
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
    assert "^src/refrncr/dataset_versions\\.py$" in gitleaks
    assert "^docs/development/(ref-51-report|supply-chain)\\.md$" in gitleaks
    assert "key,\\s+allow_unpublished=True" in gitleaks
    assert gitleaks.count('condition = "AND"') == 2


def test_codeql_analyzes_python_uploads_and_has_a_real_sarif_gate():
    workflow = load_workflow("codeql.yml")
    text = (ROOT / ".github/workflows/codeql.yml").read_text()
    assert "languages: python" in text
    pull_request = workflow["jobs"]["analyze-pull-request"]
    trusted = workflow["jobs"]["analyze-and-publish"]
    assert workflow["permissions"] == {"contents": "read"}
    assert pull_request["permissions"] == {"actions": "read", "contents": "read"}
    assert trusted["permissions"] == {
        "actions": "read",
        "contents": "read",
        "security-events": "write",
    }
    assert text.count("upload: never") == 2
    assert "upload: false" not in text
    assert "gate-sarif" in text
    assert all("upload-sarif@" not in step.get("uses", "") for step in pull_request["steps"])
    assert any("upload-sarif@" in step.get("uses", "") for step in trusted["steps"])


def test_release_attests_exact_built_wheel_and_sdist_and_generates_sbom():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert "cyclonedx-py requirements requirements/release.txt" in text
    assert "subject-path: |" in text
    assert "release-artifacts/distributions/*.whl" in text
    assert "release-artifacts/distributions/*.tar.gz" in text
    assert "dist/**" not in text
    assert re.search(r"python -m build --outdir release-artifacts/distributions", text)
