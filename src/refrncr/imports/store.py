from __future__ import annotations

import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from types import MappingProxyType

from refrncr.imports.contracts import (
    ImportConflictError,
    ImportExpiredError,
    ImportNotFoundError,
    ImportValidationError,
    StagedImport,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DATA_ROWS = 10_000
MAX_COLUMNS = 250
MAX_ZIP_ENTRIES = 1_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_REPORTED_ROWS = 100
PREVIEW_ROWS = 50
IMPORT_TTL_SECONDS = 15 * 60
MAX_STAGED_BYTES = 50 * 1024 * 1024
MAX_WORKSHEET_SOURCE_ROWS = 100_001
PROFILE_SCAN_ROWS = 100
PROFILE_SAMPLE_VALUES = 25
DISPLAY_SAMPLE_VALUES = 3
DISPLAY_SAMPLE_CHARS = 80
CANCEL_WAIT_SECONDS = 5.0

UNREADABLE_XLSX_MESSAGE = (
    "We couldn't read this modern Excel workbook. Open it in Excel or LibreOffice, save a new "
    ".xlsx copy, and upload that copy. Password-protected, damaged, or unsupported workbooks "
    "cannot be imported."
)


class TransientImportStore:
    """Bounded, process-local state for Community's single-process browser profile."""

    def __init__(
        self,
        *,
        clock=time.monotonic,
        ttl: int = IMPORT_TTL_SECONDS,
        capacity: int = 100,
        max_bytes: int = MAX_STAGED_BYTES,
        cancel_wait_seconds: float = CANCEL_WAIT_SECONDS,
    ):
        self.clock = clock
        self.ttl = ttl
        self.capacity = capacity
        self.max_bytes = max_bytes
        self.cancel_wait_seconds = cancel_wait_seconds
        self._states: dict[str, StagedImport] = {}
        self._expired: dict[str, tuple[str, str, str, float, str]] = {}
        self._operations: dict[str, int] = {}
        self._cancelling: set[str] = set()
        self._lock = threading.Lock()
        self._finished = threading.Condition(self._lock)

    @staticmethod
    def _same_state(current, expected):
        return current is not None and replace(expected, expires_at=current.expires_at) == current

    def create(
        self,
        actor_id,
        session_id,
        dataset_id,
        filename,
        content,
        kind,
        previous=None,
        *,
        concept_id="",
        mode="create",
    ) -> StagedImport:
        with self._lock:
            self._cleanup()
            if previous is not None:
                if self._states.get(previous.token) != previous or previous.busy:
                    raise ImportNotFoundError("Import not found")
            if previous is None and any(
                (item.actor_id, item.session_id, item.dataset_id)
                == (actor_id, session_id, dataset_id)
                and item.imported_count is None
                for item in self._states.values()
            ):
                raise ImportNotFoundError("Import not found")
            used_bytes = sum(len(item.content) for item in self._states.values())
            if previous is not None:
                used_bytes -= len(previous.content)
            if (
                len(self._states) - (previous is not None) >= self.capacity
                or used_bytes + len(content) > self.max_bytes
            ):
                raise ImportValidationError(
                    "Temporary import capacity is unavailable; try again later"
                )
            token = secrets.token_urlsafe(32)
            while token in self._states:
                token = secrets.token_urlsafe(32)
            state = StagedImport(
                token,
                actor_id,
                session_id,
                dataset_id,
                filename,
                content,
                kind,
                None,
                None,
                None,
                self.clock() + self.ttl,
                concept_id=concept_id,
                mode=mode,
            )
            if previous is not None:
                self._states.pop(previous.token)
            self._states[token] = state
            return state

    def get(self, token, actor_id, session_id, dataset_id) -> StagedImport:
        with self._lock:
            self._cleanup()
            state = self._states.get(token)
            if state is None or (state.actor_id, state.session_id, state.dataset_id) != (
                actor_id,
                session_id,
                dataset_id,
            ):
                expired = self._expired.get(token)
                if expired and expired[:3] == (actor_id, session_id, dataset_id):
                    raise ImportExpiredError("Import expired")
                raise ImportNotFoundError("Import not found")
            if not state.busy:
                state = replace(state, expires_at=self.clock() + self.ttl)
                self._states[token] = state
            return state

    def update(self, state: StagedImport, **changes) -> StagedImport:
        with self._lock:
            self._cleanup()
            current = self._states.get(state.token)
            if not self._same_state(current, state) or state.busy:
                raise ImportNotFoundError("Import not found")
            if changes.get("source_formats") is not None:
                changes["source_formats"] = MappingProxyType(
                    {
                        key: MappingProxyType(dict(choice))
                        for key, choice in changes["source_formats"].items()
                    }
                )
            updated = replace(state, expires_at=self.clock() + self.ttl, **changes)
            self._states[state.token] = updated
            return updated

    def active(self, actor_id, session_id, dataset_id):
        with self._lock:
            self._cleanup()
            state = next(
                (
                    state
                    for state in reversed(tuple(self._states.values()))
                    if (state.actor_id, state.session_id, state.dataset_id)
                    == (actor_id, session_id, dataset_id)
                    and state.imported_count is None
                ),
                None,
            )
            if state is not None:
                state = replace(state, expires_at=self.clock() + self.ttl)
                self._states[state.token] = state
            return state

    def claim(self, state):
        with self._lock:
            self._cleanup()
            current = self._states.get(state.token)
            if not self._same_state(current, state) or state.busy:
                raise ImportNotFoundError("Import not found")
            if state.token in self._cancelling:
                raise ImportConflictError("This import is being cancelled. Try again in a moment.")
            claimed = replace(state, expires_at=self.clock() + self.ttl, busy=True)
            self._states[state.token] = claimed
            return claimed

    def finish(self, claimed, **changes):
        # A concurrent logout/expiry must never resurrect an import.
        with self._lock:
            self._cleanup()
            current = self._states.get(claimed.token)
            if self._same_state(current, claimed):
                self._states[claimed.token] = replace(current, busy=False, **changes)
                self._finished.notify_all()

    @contextmanager
    def _operation(self, token, actor_id, session_id, dataset_id):
        """Keep a staged import alive while a request derives work from it."""
        with self._lock:
            self._cleanup()
            state = self._states.get(token)
            if state is None or (state.actor_id, state.session_id, state.dataset_id) != (
                actor_id,
                session_id,
                dataset_id,
            ):
                expired = self._expired.get(token)
                if expired and expired[:3] == (actor_id, session_id, dataset_id):
                    raise ImportExpiredError("Import expired")
                raise ImportNotFoundError("Import not found")
            if token in self._cancelling:
                raise ImportConflictError("This import is being cancelled. Try again in a moment.")
            state = replace(state, expires_at=self.clock() + self.ttl)
            self._states[token] = state
            self._operations[token] = self._operations.get(token, 0) + 1
        try:
            yield state
        finally:
            with self._lock:
                active = self._operations.get(token, 0) - 1
                if active > 0:
                    self._operations[token] = active
                else:
                    self._operations.pop(token, None)
                self._finished.notify_all()

    def consume(self, token, actor_id, session_id, dataset_id) -> StagedImport:
        with self._finished:
            deadline = time.monotonic() + self.cancel_wait_seconds
            owns_cancellation = False
            try:
                while True:
                    self._cleanup()
                    state = self._states.get(token)
                    if state is None or (
                        state.actor_id,
                        state.session_id,
                        state.dataset_id,
                    ) != (actor_id, session_id, dataset_id):
                        expired = self._expired.get(token)
                        if expired and expired[:3] == (actor_id, session_id, dataset_id):
                            raise ImportExpiredError("Import expired")
                        raise ImportNotFoundError("Import not found")
                    if not owns_cancellation:
                        if token in self._cancelling:
                            raise ImportConflictError(
                                "This import is already being cancelled. Try again in a moment."
                            )
                        self._cancelling.add(token)
                        owns_cancellation = True
                    if not state.busy and not self._operations.get(token, 0):
                        self._states.pop(token)
                        return state
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ImportConflictError(
                            "This import is still processing. Try cancelling again in a moment."
                        )
                    self._finished.wait(timeout=remaining)
            finally:
                if owns_cancellation:
                    self._cancelling.discard(token)
                    self._finished.notify_all()

    def cancel(self, token, actor_id, session_id, dataset_id) -> None:
        self.consume(token, actor_id, session_id, dataset_id)

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            tokens = [key for key, value in self._states.items() if value.session_id == session_id]
            for token in tokens:
                self._states.pop(token, None)
                self._cancelling.discard(token)
            for token in [key for key, value in self._expired.items() if value[1] == session_id]:
                self._expired.pop(token, None)
            self._finished.notify_all()

    def revoke_concept(self, concept_id):
        with self._lock:
            tokens = [k for k, state in self._states.items() if state.concept_id == concept_id]
            for token in tokens:
                self._states.pop(token, None)
                self._cancelling.discard(token)
            for token in [key for key, value in self._expired.items() if value[4] == concept_id]:
                self._expired.pop(token, None)
            self._finished.notify_all()

    def _cleanup(self):
        now = self.clock()
        for token in [
            key
            for key, value in self._states.items()
            if value.expires_at <= now
            and not self._operations.get(key, 0)
            and key not in self._cancelling
        ]:
            state = self._states.pop(token)
            self._expired[token] = (
                state.actor_id,
                state.session_id,
                state.dataset_id,
                now,
                state.concept_id,
            )
        for token in [key for key, item in self._expired.items() if item[3] + self.ttl <= now]:
            self._expired.pop(token, None)
        while len(self._expired) > self.capacity * 10:
            self._expired.pop(next(iter(self._expired)))
