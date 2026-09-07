from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from refrncr.login_security import (
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    canonical_account_key,
)
from refrncr.services import AuthenticatedPrincipal
from refrncr.web import GENERIC_LOGIN_FAILURE, create_flask_app

HTTPS = "https://community.example"
EMAIL = "user@example.com"
PASSWORD = "not-a-real-credential"


class DummySessions:
    lifetime = timedelta(hours=12)

    def __init__(self):
        self.created = 0

    def create(self, _principal):
        self.created += 1
        return "new-session-token", object()

    def revoke(self, _token):
        return None

    def resolve_with_session(self, _token):
        return None


class BlockingAuthentication:
    def __init__(self):
        self.release = threading.Event()
        self.four_entered = threading.Event()
        self.lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.peak_active = 0

    def authenticate(self, _email, _password):
        with self.lock:
            self.calls += 1
            call_number = self.calls
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            if self.active == 4:
                self.four_entered.set()
        try:
            if call_number <= 4:
                assert self.release.wait(timeout=5)
            return None
        finally:
            with self.lock:
                self.active -= 1


class OutcomeAuthentication:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def authenticate(self, _email, _password):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def make_app(authentication, limiter=None, admission=None, sessions=None):
    sessions = sessions or DummySessions()
    app = create_flask_app(
        object(),
        authentication,
        sessions,
        object(),
        object(),
        login_limiter=limiter,
        authentication_admission=admission,
    )
    app.testing = True
    return app, sessions


def csrf(client):
    response = client.get("/login", base_url=HTTPS)
    marker = "name='csrf_token' value='"
    return response.get_data(as_text=True).split(marker, 1)[1].split("'", 1)[0]


def post_login(client, token):
    return client.post(
        "/login",
        base_url=HTTPS,
        data={"email": EMAIL, "password": PASSWORD, "csrf_token": token},
        environ_base={"REMOTE_ADDR": "192.0.2.1"},
    )


def test_four_requests_enter_authentication_and_fifth_is_rejected_immediately():
    authentication = BlockingAuthentication()
    limiter = LoginAttemptLimiter()
    app, sessions = make_app(authentication, limiter=limiter)
    clients = [app.test_client() for _ in range(6)]
    tokens = [csrf(client) for client in clients]

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(post_login, clients[index], tokens[index]) for index in range(4)]
        assert authentication.four_entered.wait(timeout=5)

        rejected = post_login(clients[4], tokens[4])
        assert rejected.status_code == 401
        assert rejected.get_data(as_text=True).count(GENERIC_LOGIN_FAILURE) == 1
        assert authentication.calls == 4
        assert sessions.created == 0

        authentication.release.set()
        assert [future.result(timeout=5).status_code for future in futures] == [401] * 4

    recovered = post_login(clients[5], tokens[5])
    assert recovered.status_code == 401
    assert authentication.calls == 5
    assert authentication.peak_active == 4

    seventh = app.test_client()
    throttled = post_login(seventh, csrf(seventh))
    assert throttled.status_code == 401
    assert authentication.calls == 5


@pytest.mark.parametrize("outcome", [None, AuthenticatedPrincipal("user-id")])
def test_permit_is_released_after_normal_authentication_outcomes(outcome):
    authentication = OutcomeAuthentication(outcome)
    admission = BrowserAuthenticationAdmissionGuard(capacity=1)
    app, sessions = make_app(authentication, admission=admission)

    first = app.test_client()
    response = post_login(first, csrf(first))
    assert response.status_code == (302 if outcome else 401)

    second = app.test_client()
    response = post_login(second, csrf(second))
    assert response.status_code == (302 if outcome else 401)
    assert authentication.calls == 2
    assert sessions.created == (2 if outcome else 0)


def test_permit_is_released_after_unexpected_authentication_exception():
    authentication = OutcomeAuthentication(RuntimeError("synthetic failure"))
    admission = BrowserAuthenticationAdmissionGuard(capacity=1)
    app, _ = make_app(authentication, admission=admission)
    first = app.test_client()

    with pytest.raises(RuntimeError, match="synthetic failure"):
        post_login(first, csrf(first))

    authentication.outcome = None
    second = app.test_client()
    assert post_login(second, csrf(second)).status_code == 401
    assert authentication.calls == 2


def test_admission_rejection_creates_no_session_and_does_not_consume_limiter_attempt():
    authentication = OutcomeAuthentication(AuthenticatedPrincipal("user-id"))
    limiter = LoginAttemptLimiter(source_account_limit=1)
    admission = BrowserAuthenticationAdmissionGuard(capacity=1)
    held_permit = admission.try_acquire()
    assert held_permit is not None
    app, sessions = make_app(authentication, limiter=limiter, admission=admission)
    rejected_client = app.test_client()

    rejected = post_login(rejected_client, csrf(rejected_client))
    assert rejected.status_code == 401
    assert rejected.get_data(as_text=True).count(GENERIC_LOGIN_FAILURE) == 1
    assert authentication.calls == 0
    assert sessions.created == 0

    held_permit.__exit__(None, None, None)
    accepted_client = app.test_client()
    accepted = post_login(accepted_client, csrf(accepted_client))
    assert accepted.status_code == 302
    assert authentication.calls == 1
    assert sessions.created == 1


def test_guard_has_no_waiting_queue_and_recovers_all_capacity():
    guard = BrowserAuthenticationAdmissionGuard()
    permits = [guard.try_acquire() for _ in range(4)]
    assert all(permit is not None for permit in permits)
    assert guard.try_acquire() is None
    for permit in permits:
        permit.__exit__(None, None, None)
    recovered = [guard.try_acquire() for _ in range(4)]
    assert all(permit is not None for permit in recovered)
    for permit in recovered:
        permit.__exit__(None, None, None)


def test_cancelled_reservation_removes_only_that_concurrent_attempt():
    limiter = LoginAttemptLimiter(source_limit=5, source_account_limit=5)
    account = canonical_account_key(EMAIL)
    first = limiter.reserve("192.0.2.1", account)
    second = limiter.reserve("192.0.2.1", account)
    assert first is not None and second is not None
    first.cancel()
    assert limiter.allow("192.0.2.1", account)
    assert limiter.allow("192.0.2.1", account)
    assert limiter.allow("192.0.2.1", account)
    assert limiter.allow("192.0.2.1", account)
    assert not limiter.allow("192.0.2.1", account)


def test_failure_completing_after_concurrent_success_remains_counted():
    limiter = LoginAttemptLimiter(source_limit=20, source_account_limit=2)
    account = canonical_account_key(EMAIL)
    successful = limiter.reserve("192.0.2.1", account)
    concurrent_failure = limiter.reserve("192.0.2.1", account)
    assert successful is not None and concurrent_failure is not None

    successful.succeeded()
    concurrent_failure.failed()

    assert limiter.allow("192.0.2.1", account)
    assert not limiter.allow("192.0.2.1", account)


def test_failure_completing_before_concurrent_success_is_cleared():
    limiter = LoginAttemptLimiter(source_limit=20, source_account_limit=2)
    account = canonical_account_key(EMAIL)
    successful = limiter.reserve("192.0.2.1", account)
    prior_failure = limiter.reserve("192.0.2.1", account)
    assert successful is not None and prior_failure is not None

    prior_failure.failed()
    successful.succeeded()

    assert limiter.allow("192.0.2.1", account)
    assert limiter.allow("192.0.2.1", account)
    assert not limiter.allow("192.0.2.1", account)
