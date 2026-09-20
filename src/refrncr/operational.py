"""Safe health checks, request correlation and operational JSON logging."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.responses import JSONResponse

READINESS_TIMEOUT_SECONDS = 2.0
REQUEST_ID_HEADER = b"x-request-id"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
LOGGER_NAME = "refrncr.operational"
CURRENT_REQUEST_ID: ContextVar[str | None] = ContextVar("refrncr_current_request_id", default=None)


def resolve_log_level(value: str | None = None) -> tuple[int, bool]:
    """Return a safe configured level and whether invalid input was replaced."""
    configured = value if value is not None else os.environ.get("REFRNCR_LOG_LEVEL", "INFO")
    normalized = configured.strip().upper()
    if normalized not in VALID_LOG_LEVELS:
        return logging.INFO, True
    return logging.getLevelNamesMapping()[normalized], False


class OperationalJSONFormatter(logging.Formatter):
    """Serialize allowlisted operational fields without exception or message leakage."""

    fields = (
        "event",
        "request_id",
        "http_method",
        "route",
        "status_code",
        "duration_ms",
        "error_category",
    )

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        fallback_error_category = None
        if event is None and record.levelno >= logging.ERROR:
            event = "runtime.failure"
            message = record.getMessage().lower()
            fallback_error_category = (
                "address_in_use" if "address already in use" in message else "runtime_error"
            )
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "severity": record.levelname,
            "event": event or "runtime.message",
        }
        for field in self.fields[1:]:
            value = getattr(record, field, None)
            if field == "request_id" and value is None:
                value = CURRENT_REQUEST_ID.get()
            if field == "error_category" and value is None:
                value = fallback_error_category
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_operational_logging() -> None:
    """Configure process logging as one safe JSON object per stdout line."""
    level, invalid = resolve_log_level()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(OperationalJSONFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    if invalid:
        logging.getLogger(LOGGER_NAME).warning(
            "Invalid log level replaced",
            extra={"event": "logging.configuration_invalid"},
        )


def accepted_request_id(headers: list[tuple[bytes, bytes]]) -> str:
    values = [value for name, value in headers if name.lower() == REQUEST_ID_HEADER]
    if len(values) == 1:
        try:
            candidate = values[0].decode("ascii")
        except UnicodeDecodeError:
            candidate = ""
        if REQUEST_ID_PATTERN.fullmatch(candidate):
            return candidate
    return str(uuid.uuid4())


def _route_template(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    root_path = scope.get("root_path")
    if isinstance(path, str) and path.startswith("/"):
        if isinstance(root_path, str) and root_path not in {"", "/"}:
            return root_path.rstrip("/") + path
        if path == "/" and scope.get("path") != "/":
            return "/{browser_path}"
        return path
    if isinstance(root_path, str) and root_path.startswith("/"):
        return root_path or "/"
    if isinstance(scope.get("path"), str) and scope["path"].startswith("/"):
        return "/{browser_path}"
    return "unmatched"


class OperationalMiddleware:
    """Add correlation headers and emit one privacy-safe completion event."""

    def __init__(self, app) -> None:
        self.app = app
        self.logger = logging.getLogger(LOGGER_NAME)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = accepted_request_id(scope.get("headers", []))
        request_token = CURRENT_REQUEST_ID.set(request_id)
        scope["refrncr.request_id"] = request_id
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message):
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != REQUEST_ID_HEADER
                ]
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        error_category = None
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            error_category = "unexpected_exception"
            if not response_started:
                response = JSONResponse({"status": "unavailable"}, status_code=500)
                await response(scope, receive, send_with_request_id)
            else:
                raise
        finally:
            route_template = _route_template(scope)
            if error_category is not None:
                level = logging.ERROR
            elif status_code == 429 or (route_template == "/readyz" and status_code == 503):
                level = logging.WARNING
            elif status_code >= 500:
                level = logging.ERROR
            else:
                level = logging.INFO
            self.logger.log(
                level,
                "Request completed",
                extra={
                    "event": "http.request_completed",
                    "request_id": request_id,
                    "http_method": scope.get("method", "UNKNOWN"),
                    "route": route_template,
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error_category": error_category,
                },
            )
            CURRENT_REQUEST_ID.reset(request_token)


def expected_database_revision() -> str:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError("application migration history must have exactly one head")
    return heads[0]


def database_is_ready(engine: Engine) -> bool:
    """Check connectivity and the applied revision using the configured engine."""
    expected = expected_database_revision()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        applied = connection.scalar(text("SELECT version_num FROM alembic_version"))
    return applied == expected


async def readiness_status(engine: Engine) -> bool:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(database_is_ready, engine), timeout=READINESS_TIMEOUT_SECONDS
        )
    except Exception:
        return False


def operational_response(status: str, request_id: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        {"status": status},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
    )
