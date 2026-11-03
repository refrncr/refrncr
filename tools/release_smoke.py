"""Probe the exact pipx-installed release artifact through its public HTTP surface."""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request


def wait_for(url: str, *, timeout: float = 30) -> bytes:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP status for {url}: {response.status}")
                return response.read()
        except (OSError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"release smoke server did not become ready: {url}") from None
            time.sleep(0.2)


def main(base_url: str) -> None:
    if wait_for(f"{base_url}/healthz") != b'{"status":"ok"}':
        raise RuntimeError("health response did not match the packaged application contract")
    if b"refrncr" not in wait_for(f"{base_url}/"):
        raise RuntimeError("packaged template response did not contain the application name")
    if not wait_for(f"{base_url}/static/css/base.css"):
        raise RuntimeError("packaged static asset was empty")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: release_smoke.py BASE_URL")
    main(sys.argv[1].rstrip("/"))
