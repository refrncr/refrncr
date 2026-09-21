"""Small, testable supply-chain policy gates used by CI and release workflows."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REQUIRED_EXCEPTION_FIELDS = {
    "advisory_id",
    "package",
    "version",
    "risk_assessment",
    "mitigation",
    "owner",
    "approver",
    "created_on",
    "expires_on",
    "remediation_plan",
}


def load_exceptions(path: Path, *, today: date | None = None) -> list[dict[str, str]]:
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("exceptions"), list):
        raise ValueError("exception register must use schema_version 1 and an exceptions array")
    current = today or date.today()
    validated = []
    for index, entry in enumerate(data["exceptions"]):
        missing = sorted(REQUIRED_EXCEPTION_FIELDS - entry.keys())
        if missing:
            raise ValueError(f"exception {index} is missing fields: {', '.join(missing)}")
        if any(
            not isinstance(entry[field], str) or not entry[field].strip()
            for field in REQUIRED_EXCEPTION_FIELDS
        ):
            raise ValueError(f"exception {index} contains an empty or non-string field")
        created = date.fromisoformat(entry["created_on"])
        expires = date.fromisoformat(entry["expires_on"])
        if expires < created:
            raise ValueError(f"exception {index} expires before it is created")
        if (expires - created).days > 30:
            raise ValueError(f"exception {index} lasts longer than 30 days")
        if expires < current:
            raise ValueError(f"exception {index} expired on {expires.isoformat()}")
        validated.append(entry)
    return validated


def findings_from_audit(report: dict) -> list[tuple[str, str, str]]:
    findings = []
    for dependency in report.get("dependencies", []):
        for vulnerability in dependency.get("vulns", []):
            findings.append(
                (
                    dependency["name"].lower().replace("_", "-"),
                    dependency["version"],
                    vulnerability["id"],
                )
            )
    return findings


def gate_audit(report: dict, exceptions: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    allowed = {
        (entry["package"].lower().replace("_", "-"), entry["version"], entry["advisory_id"])
        for entry in exceptions
    }
    return [finding for finding in findings_from_audit(report) if finding not in allowed]


def gate_sarif(report: dict) -> list[str]:
    actionable = []
    for run in report.get("runs", []):
        for result in run.get("results", []):
            if result.get("level", "warning") in {"error", "warning"}:
                actionable.append(result.get("ruleId", "unknown-rule"))
    return actionable


def validate_action_pins(root: Path) -> list[str]:
    errors = []
    sha = re.compile(r"^[0-9a-f]{40}$")
    for workflow in sorted((root / ".github" / "workflows").glob("*.yml")):
        for line_number, line in enumerate(workflow.read_text().splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if not match:
                continue
            reference = match.group(1)
            if reference.startswith("./"):
                continue
            if "@" not in reference or not sha.fullmatch(reference.rsplit("@", 1)[1]):
                errors.append(
                    f"{workflow}:{line_number}: external action is not pinned to a full SHA"
                )
    return errors


def run_audit(requirements: Path, register: Path, output: Path) -> int:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "-r",
            str(requirements),
            "-f",
            "json",
            "-o",
            str(output),
        ],
        check=False,
    )
    if not output.exists():
        return completed.returncode or 2
    report = json.loads(output.read_text())
    blocked = gate_audit(report, load_exceptions(register))
    if blocked:
        for package, version, advisory in blocked:
            print(
                f"blocking runtime vulnerability: {package}=={version} ({advisory})",
                file=sys.stderr,
            )
        return 1
    print(f"runtime audit passed; findings={len(findings_from_audit(report))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-exceptions")
    validate.add_argument("register", type=Path)
    pins = subparsers.add_parser("validate-action-pins")
    pins.add_argument("root", type=Path, nargs="?", default=Path.cwd())
    audit = subparsers.add_parser("audit-runtime")
    audit.add_argument("requirements", type=Path)
    audit.add_argument("register", type=Path)
    audit.add_argument("output", type=Path)
    sarif = subparsers.add_parser("gate-sarif")
    sarif.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate-exceptions":
            load_exceptions(args.register)
        elif args.command == "validate-action-pins":
            errors = validate_action_pins(args.root)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
        elif args.command == "audit-runtime":
            return run_audit(args.requirements, args.register, args.output)
        else:
            actionable = gate_sarif(json.loads(args.report.read_text()))
            if actionable:
                print(
                    "actionable CodeQL results: " + ", ".join(sorted(set(actionable))),
                    file=sys.stderr,
                )
                return 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"supply-chain policy error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
