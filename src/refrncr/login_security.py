from __future__ import annotations

import hashlib
import ipaddress
import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import islice

DEFAULT_SOURCE_LIMIT = 20
DEFAULT_SOURCE_ACCOUNT_LIMIT = 5
DEFAULT_WINDOW = timedelta(minutes=5)
DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_AUTHENTICATION_CAPACITY = 4
MAX_FORWARDED_FOR_LENGTH = 4096
MAX_FORWARDED_ADDRESSES = 32
MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS = 320
OVERSIZED_ACCOUNT_KEY = hashlib.sha256(b"oversized-browser-login-identifier").hexdigest()


def canonical_account_key(identifier: str) -> str:
    """Return a fixed-size key without querying or validating account existence."""
    if len(identifier) > MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS:
        return OVERSIZED_ACCOUNT_KEY
    canonical = identifier.strip().casefold()
    encoded = canonical.encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceIdentityResolver:
    trusted_proxies: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()

    @classmethod
    def from_config(cls, values: Iterable[str] = ()) -> SourceIdentityResolver:
        networks = tuple(ipaddress.ip_network(value.strip(), strict=False) for value in values)
        return cls(networks)

    def resolve(self, direct_peer: str | None, forwarded_for: str | None = None) -> str:
        direct = self._address(direct_peer)
        if direct is None:
            return "unknown"
        if not self._trusted(direct) or not forwarded_for:
            return direct.compressed
        forwarded = self._forwarded_addresses(forwarded_for)
        if forwarded is None:
            return direct.compressed
        for address in reversed(forwarded):
            if not self._trusted(address):
                return address.compressed
        return direct.compressed

    def _trusted(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(
            address.version == network.version and address in network
            for network in self.trusted_proxies
        )

    @staticmethod
    def _address(value: str | None):
        try:
            return ipaddress.ip_address(value) if value else None
        except ValueError:
            return None

    @classmethod
    def _forwarded_addresses(cls, value: str):
        if len(value) > MAX_FORWARDED_FOR_LENGTH:
            return None
        parts = [part.strip() for part in value.split(",")]
        if not parts or len(parts) > MAX_FORWARDED_ADDRESSES or any(not part for part in parts):
            return None
        addresses = [cls._address(part) for part in parts]
        if any(address is None for address in addresses):
            return None
        return addresses


@dataclass(slots=True)
class _AttemptEntry:
    attempts: deque[_Attempt] = field(default_factory=deque)


@dataclass(slots=True)
class _Attempt:
    timestamp: datetime
    identifier: int
    completed: bool = False


class LoginAttemptReservation:
    def __init__(
        self, limiter: LoginAttemptLimiter, source: str, account: str, identifier: int
    ) -> None:
        self._limiter = limiter
        self._source = source
        self._account = account
        self._identifier = identifier
        self._finished = False

    def cancel(self) -> None:
        if not self._finished:
            self._limiter._cancel(self._source, self._account, self._identifier)
            self._finished = True

    def failed(self) -> None:
        if not self._finished:
            self._limiter._complete_failure(self._source, self._account, self._identifier)
            self._finished = True

    def succeeded(self) -> None:
        if not self._finished:
            self._limiter._complete_success(self._source, self._account, self._identifier)
            self._finished = True


class LoginAttemptLimiter:
    """Thread-safe, process-local limiter with bounded deterministic storage."""

    def __init__(
        self,
        *,
        source_limit: int = DEFAULT_SOURCE_LIMIT,
        source_account_limit: int = DEFAULT_SOURCE_ACCOUNT_LIMIT,
        window: timedelta = DEFAULT_WINDOW,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cleanup_budget: int = 16,
    ) -> None:
        if min(source_limit, source_account_limit, cleanup_budget) < 1 or max_entries < 2:
            raise ValueError("Limiter limits must be positive")
        self.source_limit = source_limit
        self.source_account_limit = source_account_limit
        self.window = window
        self.max_entries = max_entries
        self.clock = clock
        self.cleanup_budget = cleanup_budget
        self._entries: OrderedDict[tuple[str, ...], _AttemptEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._next_identifier = 0

    def allow(self, source: str, account: str) -> bool:
        return self.reserve(source, account) is not None

    def reserve(self, source: str, account: str) -> LoginAttemptReservation | None:
        now = self.clock()
        source_key = ("source", source)
        pair_key = ("pair", source, account)
        with self._lock:
            self._cleanup_expired(now)
            source_entry = self._current(source_key, now)
            pair_entry = self._current(pair_key, now)
            if (
                len(source_entry.attempts) >= self.source_limit
                or len(pair_entry.attempts) >= self.source_account_limit
            ):
                return None
            self._next_identifier += 1
            attempt = _Attempt(now, self._next_identifier)
            source_entry.attempts.append(attempt)
            pair_entry.attempts.append(attempt)
            return LoginAttemptReservation(self, source, account, attempt.identifier)

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def tracked_keys(self) -> tuple[tuple[str, ...], ...]:
        with self._lock:
            return tuple(self._entries)

    def _current(self, key: tuple[str, ...], now: datetime) -> _AttemptEntry:
        entry = self._entries.get(key)
        if entry is None:
            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)
            entry = _AttemptEntry()
            self._entries[key] = entry
        else:
            self._entries.move_to_end(key)
        self._prune(entry, now)
        return entry

    def _cleanup_expired(self, now: datetime) -> None:
        for key in tuple(islice(self._entries, self.cleanup_budget)):
            entry = self._entries[key]
            self._prune(entry, now)
            if not entry.attempts:
                del self._entries[key]

    def _prune(self, entry: _AttemptEntry, now: datetime) -> None:
        cutoff = now - self.window
        while entry.attempts and entry.attempts[0].timestamp <= cutoff:
            entry.attempts.popleft()

    def _cancel(self, source: str, account: str, identifier: int) -> None:
        with self._lock:
            for key in (("source", source), ("pair", source, account)):
                entry = self._entries.get(key)
                if entry is None:
                    continue
                for attempt in entry.attempts:
                    if attempt.identifier == identifier:
                        entry.attempts.remove(attempt)
                        break
                if not entry.attempts:
                    self._entries.pop(key, None)

    def _complete_failure(self, source: str, account: str, identifier: int) -> None:
        with self._lock:
            entry = self._entries.get(("pair", source, account))
            if entry is None:
                return
            for attempt in entry.attempts:
                if attempt.identifier == identifier:
                    attempt.completed = True
                    return

    def _complete_success(self, source: str, account: str, identifier: int) -> None:
        with self._lock:
            key = ("pair", source, account)
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.attempts = deque(
                attempt
                for attempt in entry.attempts
                if not attempt.completed and attempt.identifier != identifier
            )
            if not entry.attempts:
                self._entries.pop(key, None)


class BrowserAuthenticationAdmissionGuard:
    """Non-blocking process-local capacity guard for browser authentication."""

    def __init__(self, capacity: int = DEFAULT_AUTHENTICATION_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("Authentication admission capacity must be positive")
        self.capacity = capacity
        self._semaphore = threading.BoundedSemaphore(capacity)

    def try_acquire(self) -> _AdmissionPermit | None:
        if not self._semaphore.acquire(blocking=False):
            return None
        return _AdmissionPermit(self._semaphore)


class _AdmissionPermit:
    def __init__(self, semaphore: threading.BoundedSemaphore) -> None:
        self._semaphore = semaphore

    def __enter__(self) -> _AdmissionPermit:
        return self

    def __exit__(self, *_exc_info) -> None:
        self._semaphore.release()
