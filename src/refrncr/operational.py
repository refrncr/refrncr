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
from typing import Any, TextIO

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
INTERACTIVE_OUTPUT = False
INTERACTIVE_DEBUG = False


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
        "authentication",
        "error_category",
    )

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(operational_event(record), separators=(",", ":"), ensure_ascii=True)


def operational_event(record: logging.LogRecord) -> dict[str, Any]:
    """Build the single allowlisted event representation used by every renderer."""
    event = getattr(record, "event", None)
    fallback_error_category = None
    if event is None and record.levelno >= logging.ERROR:
        event = "runtime.failure"
        message = record.getMessage().lower()
        fallback_error_category = (
            "address_in_use" if "address already in use" in message else "runtime_error"
        )
    payload: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
        "severity": record.levelname,
        "event": event or "runtime.message",
    }
    for field in OperationalJSONFormatter.fields[1:]:
        value = getattr(record, field, None)
        if field == "request_id" and value is None:
            value = CURRENT_REQUEST_ID.get()
        if field == "error_category" and value is None:
            value = fallback_error_category
        if value is not None:
            payload[field] = value
    return payload


class InteractiveTerminalHandler(logging.Handler):
    """Render compact, safe operational feedback for a human terminal."""

    colors = {"ready": "\033[32m", "warning": "\033[33m", "error": "\033[31m"}

    def __init__(
        self,
        stream: TextIO,
        *,
        host: str,
        port: int,
        color: bool,
        minimum_level: int = logging.INFO,
    ) -> None:
        super().__init__()
        self.stream = stream
        self.host = host
        self.port = port
        self.color = color
        self.minimum_level = minimum_level
        self._last_debug_key: tuple[Any, ...] | None = None
        self._repeated_debug_events = 0

    def _accent(self, text: str, kind: str) -> str:
        return f"{self.colors[kind]}{text}\033[0m" if self.color else text

    def _write(self, text: str) -> None:
        self.stream.write(text + "\n")
        self.flush()

    def _flush_debug_summary(self) -> None:
        if self._repeated_debug_events:
            self._write(f"  {self._repeated_debug_events} equivalent debug requests suppressed")
        self._repeated_debug_events = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = operational_event(record)
            event = payload["event"]
            if event == "application.started":
                browser_host = "localhost" if self.host == "0.0.0.0" else self.host
                self._write(
                    self._accent(
                        f"\N{CHECK MARK} refrncr is starting at http://{browser_host}:{self.port}",
                        "ready",
                    )
                )
                if self.host == "0.0.0.0":
                    self._write("  Listening on all network interfaces (0.0.0.0).")
                self._write("  Press Ctrl+C to stop.")
            elif event == "application.stopped":
                self._flush_debug_summary()
                self._write("refrncr stopped.")
            elif event == "http.request_completed" and record.levelno == logging.DEBUG:
                key = (
                    payload.get("route"),
                    payload.get("status_code"),
                    payload.get("error_category"),
                )
                if key == self._last_debug_key:
                    self._repeated_debug_events += 1
                    return
                self._flush_debug_summary()
                self._last_debug_key = key
                detail = (
                    f"DEBUG {payload.get('http_method', 'UNKNOWN')} "
                    f"{payload.get('route', 'unmatched')} {payload.get('status_code', 500)} "
                    f"{payload.get('duration_ms', 0)}ms request_id={payload.get('request_id', '-')}"
                )
                if payload.get("error_category"):
                    detail += f" error={payload['error_category']}"
                self._write(detail)
            elif record.levelno >= logging.WARNING and record.levelno >= self.minimum_level:
                self._flush_debug_summary()
                category = payload.get("error_category")
                if category == "address_in_use":
                    message = (
                        "refrncr could not start: the port is already in use; choose another port."
                    )
                elif record.levelno >= logging.ERROR:
                    message = "refrncr encountered a runtime error; check configuration and retry."
                else:
                    message = f"refrncr warning: {event}"
                kind = "error" if record.levelno >= logging.ERROR else "warning"
                self._write(self._accent(message, kind))
        except Exception:  # noqa: BLE001 - logging failure must not break application flow.
            self.handleError(record)

    def close(self) -> None:
        self._flush_debug_summary()
        super().close()


def configure_operational_logging(
    *,
    log_level: str | None = None,
    interactive: bool = False,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Configure safe JSON collector output or compact interactive output."""
    global INTERACTIVE_DEBUG, INTERACTIVE_OUTPUT
    INTERACTIVE_OUTPUT = interactive
    level, invalid = resolve_log_level(log_level)
    INTERACTIVE_DEBUG = interactive and level <= logging.DEBUG
    if interactive:
        handler = InteractiveTerminalHandler(
            sys.stdout,
            host=host,
            port=port,
            color="NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb",
            minimum_level=level,
        )
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(OperationalJSONFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if interactive else level)
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
                level = logging.DEBUG if INTERACTIVE_OUTPUT and INTERACTIVE_DEBUG else logging.INFO
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
                    "authentication": scope.get("refrncr.authentication"),
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
    except Exception:  # noqa: BLE001 - readiness is deliberately fail-closed.
        return False


def operational_response(status: str, request_id: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        {"status": status},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
    )
