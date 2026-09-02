from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

HTTPS_SESSION_COOKIE = "__Host-refrncr_session"
LOOPBACK_SESSION_COOKIE = "refrncr_session"
HTTPS_ANONYMOUS_CSRF_COOKIE = "__Host-refrncr_csrf"
LOOPBACK_ANONYMOUS_CSRF_COOKIE = "refrncr_csrf"


def is_loopback_host(value: str | None) -> bool:
    """Recognize only literal loopback IPs and localhost, without DNS lookup."""
    if not value:
        return False
    try:
        hostname = urlsplit(f"//{value}").hostname
    except ValueError:
        return False
    if hostname is None:
        return False
    hostname = hostname.rstrip(".").lower()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class BrowserCookieSettings:
    session_name: str
    anonymous_csrf_name: str
    secure: bool
    session_max_age: int

    @property
    def common_options(self) -> dict[str, object]:
        return {
            "secure": self.secure,
            "httponly": True,
            "samesite": "Lax",
            "path": "/",
        }


@dataclass(frozen=True, slots=True)
class BrowserCookiePolicy:
    session_max_age: int
    allow_loopback_http: bool = False

    def for_request(
        self, *, scheme: str, host: str | None, remote_address: str | None
    ) -> BrowserCookieSettings | None:
        if scheme == "https":
            return BrowserCookieSettings(
                HTTPS_SESSION_COOKIE,
                HTTPS_ANONYMOUS_CSRF_COOKIE,
                True,
                self.session_max_age,
            )
        if (
            scheme == "http"
            and self.allow_loopback_http
            and is_loopback_host(host)
            and is_loopback_host(remote_address)
        ):
            return BrowserCookieSettings(
                LOOPBACK_SESSION_COOKIE,
                LOOPBACK_ANONYMOUS_CSRF_COOKIE,
                False,
                self.session_max_age,
            )
        return None
