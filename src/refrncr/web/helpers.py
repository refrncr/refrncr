"""Small presentation and form helpers shared by browser routes."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from flask import abort, request

from refrncr.services import ValidationError, parse_expected_revision


def merge_error_messages(*messages: str | None) -> str:
    """Join distinct non-empty validation messages while preserving order."""
    return " ".join(dict.fromkeys(message for message in messages if message))


def is_safe_login_target(target: str | None) -> bool:
    """Return whether a login target is a safe root-relative path."""
    if not target:
        return True
    decoded = unquote(target)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in decoded
        )
        or any(character in decoded for character in "\\%?#")
        or any(segment in {".", ".."} for segment in decoded.split("/"))
    ):
        return False
    parsed = urlsplit(decoded)
    return not (parsed.scheme or parsed.netloc or parsed.path in {"/login", "/logout", "/setup"})


def safe_login_target(target: str | None) -> str:
    """Accept only root-relative paths, never URLs or query/fragment payloads."""
    return target if is_safe_login_target(target) else "/"


def parse_page_parameter() -> int:
    """Read a single canonical positive page number from the query string."""
    values = request.args.getlist("page")
    if not values:
        return 1
    if len(values) != 1 or re.fullmatch(r"[1-9][0-9]{0,8}", values[0]) is None:
        abort(400, description="Page must be a canonical positive integer")
    return int(values[0])


def exact_expected_revision() -> int:
    """Read exactly one optimistic-lock revision from the submitted form."""
    values = request.form.getlist("expected_revision")
    if len(values) != 1:
        raise ValidationError("Submit exactly one expected revision")
    return parse_expected_revision(values[0])
