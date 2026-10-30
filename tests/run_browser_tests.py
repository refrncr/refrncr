"""Run disposable Chromium acceptance fixtures, individually, as core set, or in full."""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
# Each script gets a fresh in-memory application, including scripts sharing a fixture.
CASES = {
    "web_csp": ("select", 5246),
    "view_transitions": ("table_rows", 5144),
    "access_management": ("table_rows", 5144),
    "api_docs": ("rest_api", 5147),
    "api_keys": ("api_keys", 5140),
    "audit_export": ("table_rows", 5144),
    "audit_history": ("audit_history", 5145),
    "audit_minutes": ("audit_history", 5145),
    "concept": ("concept", 5185),
    "concept_discard": ("discard", 5186),
    "dataset_creation": ("table_rows", 5144),
    "dataset_deletion": ("table_rows", 5144),
    "dataset_diff": ("dataset_diff", 5184),
    "dataset_versions": ("dataset_versions", 5183),
    "field_datatypes": ("field_datatypes", 5146),
    "mapping": ("mapping", 5193),
    "mapping_usage": ("mapping_usage", 5194),
    "navigation": ("table_rows", 5144),
    "navigation_tabs": ("mapping", 5193),
    "new_version": ("new_version", 5187),
    "overview": ("overview", 5195),
    "record_search": ("record_search", 5136),
    "ref165_regressions": ("presentation_metadata", 5165),
    "ref259_headers": ("ui_polish", 5162),
    "rest_api": ("rest_api", 5147),
    "select": ("select", 5246),
    "source_formats": ("source_formats", 5154),
    "source_verdicts": ("source_formats", 5154),
    "table_rows": ("table_rows", 5144),
    "ui_polish": ("ui_polish", 5162),
    "ui_refresh": ("ui_refresh", 5186),
}

# Short pull-request gate: CSP/navigation, critical dataset lifecycle, search and
# access control, plus the REF-294 REST installation policy. The nightly job runs
# every case so this list can stay intentionally focused and fast.
CORE_CASES = (
    "web_csp",
    "view_transitions",
    "dataset_creation",
    "new_version",
    "dataset_deletion",
    "access_management",
    "rest_api",
)


def run(name):
    fixture, port = CASES[name]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="refrncr-browser-") as directory:
        env = {
            **os.environ,
            "REFRNCR_BROWSER_URL": base,
            "REFRNCR_BROWSER_EMAIL": "admin@example.com",
            "REFRNCR_BROWSER_PASSWORD": "correct horse battery staple",
            "REFRNCR_PYTHON": sys.executable,
            "REFRNCR_BROWSER_OUTPUT": (
                str(Path(directory) / "mapping-usage.png") if name == "mapping_usage" else directory
            ),
        }
        with tempfile.TemporaryFile(mode="w+") as log:
            server = subprocess.Popen(
                [sys.executable, str(ROOT / f"tests/browser_{fixture}_app.py")],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("Browser fixture exited before startup")
                    try:
                        with urlopen(base + "/login", timeout=1):
                            break
                    except (URLError, TimeoutError):
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Browser fixture did not become ready")
                subprocess.run(
                    ["node", str(ROOT / f"tests/javascript/{name}_browser.cjs")],
                    cwd=ROOT,
                    env=env,
                    check=True,
                    timeout=240,
                )
            except Exception:
                log.seek(0)
                print(log.read()[-4000:], file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", help="case names (default: web_csp)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--core", action="store_true", help="run the pull-request core set")
    group.add_argument("--all", action="store_true", help="run the complete acceptance suite")
    args = parser.parse_args()
    if args.cases and (args.core or args.all):
        parser.error("case names cannot be combined with --core or --all")
    if args.all:
        names = list(CASES)
    elif args.core:
        names = list(CORE_CASES)
    else:
        names = args.cases or ["web_csp"]
    if set(names) - CASES.keys():
        parser.error("Unknown case; choose from " + ", ".join(CASES))
    failed = []
    for name in names:
        print(f"Browser case: {name}", flush=True)
        try:
            run(name)
        except (subprocess.SubprocessError, RuntimeError) as exc:
            print(f"FAIL {name}: {exc}", flush=True)
            failed.append(name)
        else:
            print(f"PASS {name}", flush=True)
    print(f"Browser results: {len(names) - len(failed)} passed, {len(failed)} failed", flush=True)
    return bool(failed)


if __name__ == "__main__":
    sys.exit(main())
