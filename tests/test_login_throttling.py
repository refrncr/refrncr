from __future__ import annotations

import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy.pool import StaticPool
from support import html_text

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.login_security import (
    MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS,
    OVERSIZED_ACCOUNT_KEY,
    BrowserAuthenticationAdmissionGuard,
    LoginAttemptLimiter,
    SourceIdentityResolver,
    canonical_account_key,
)
from refrncr.services import ResourceTypeRegistry
from refrncr.web import GENERIC_LOGIN_FAILURE, create_flask_app

PASSWORD = "correct horse battery staple"
HTTPS = "https://community.example"


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self):
        return self.value


def csrf(response) -> str:
    marker = "name='csrf_token' value='"
    return response.get_data(as_text=True).split(marker, 1)[1].split("'", 1)[0]


def generic_login_failure_body(response) -> str:
    body = response.get_data(as_text=True)
    assert html_text(body).count(GENERIC_LOGIN_FAILURE) == 1
    body = re.sub(r"(name='email' type='email' value=')[^']*", r"\1EMAIL", body)
    return re.sub(r"name='csrf_token' value='[^']+'", "name='csrf_token' value='TOKEN'", body)


def configured_web(limiter: LoginAttemptLimiter, admission=None):
    engine = create_database_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    services = create_community_services(create_session_factory(engine), ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    client = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        login_limiter=limiter,
        authentication_admission=admission,
    ).test_client()
    token = csrf(client.get("/setup", base_url=HTTPS))
    response = client.post(
        "/setup",
        base_url=HTTPS,
        data={
            "email": "admin@example.com",
            "display_name": "Administrator",
            "password": PASSWORD,
            "csrf_token": token,
        },
    )
    assert response.status_code == 302
    return services, client


def post_login(client, email: str, password: str, **kwargs):
    token = csrf(client.get("/login", base_url=HTTPS, **kwargs))
    return client.post(
        "/login",
        base_url=HTTPS,
        data={"email": email, "password": password, "csrf_token": token},
        **kwargs,
    )


def test_source_account_threshold_and_exact_boundary():
    limiter = LoginAttemptLimiter(source_limit=10, source_account_limit=5)
    account = canonical_account_key(" User@Example.com ")

    assert [limiter.allow("192.0.2.1", account) for _ in range(5)] == [True] * 5
    assert not limiter.allow("192.0.2.1", account)


def test_source_threshold_applies_across_submitted_accounts():
    limiter = LoginAttemptLimiter()

    for index in range(20):
        assert limiter.allow("192.0.2.1", canonical_account_key(f"user-{index}@example.com"))
    assert not limiter.allow("192.0.2.1", canonical_account_key("next@example.com"))


def test_recovery_after_window_uses_injected_clock():
    clock = MutableClock()
    limiter = LoginAttemptLimiter(source_limit=2, source_account_limit=2, clock=clock)
    account = canonical_account_key("user@example.com")
    assert limiter.allow("192.0.2.1", account)
    assert limiter.allow("192.0.2.1", account)
    assert not limiter.allow("192.0.2.1", account)

    clock.value += timedelta(minutes=5)
    assert limiter.allow("192.0.2.1", account)


def test_success_reset_clears_only_pair_and_preserves_source_history():
    limiter = LoginAttemptLimiter(source_limit=4, source_account_limit=3)
    first = canonical_account_key("first@example.com")
    second = canonical_account_key("second@example.com")
    failure = limiter.reserve("192.0.2.1", first)
    success = limiter.reserve("192.0.2.1", first)
    assert failure is not None and success is not None
    failure.failed()
    success.succeeded()

    assert limiter.allow("192.0.2.1", first)
    assert limiter.allow("192.0.2.1", second)
    assert not limiter.allow("192.0.2.1", canonical_account_key("third@example.com"))


def test_storage_is_bounded_and_eviction_is_deterministic():
    limiter = LoginAttemptLimiter(max_entries=4, source_limit=20, source_account_limit=5)
    first = canonical_account_key("first@example.com")
    second = canonical_account_key("second@example.com")
    third = canonical_account_key("third@example.com")
    assert limiter.allow("192.0.2.1", first)
    assert limiter.allow("192.0.2.2", second)
    assert limiter.allow("192.0.2.3", third)

    assert limiter.entry_count == 4
    assert limiter.tracked_keys == (
        ("source", "192.0.2.2"),
        ("pair", "192.0.2.2", second),
        ("source", "192.0.2.3"),
        ("pair", "192.0.2.3", third),
    )


def test_default_storage_bound_survives_high_cardinality_malformed_identifiers():
    limiter = LoginAttemptLimiter()
    for index in range(10_050):
        account = canonical_account_key(f"not-an-email-{index}-{'x' * 1000}")
        assert len(account) == 64
        limiter.allow(f"192.0.{index // 256 % 256}.{index % 256}", account)
    assert limiter.entry_count == 10_000


def test_oversized_identifiers_use_one_fixed_key_without_canonicalizing_content():
    first = "A" * (MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS + 1)
    second = "B" * 1_000_000
    assert canonical_account_key(first) == OVERSIZED_ACCOUNT_KEY
    assert canonical_account_key(second) == OVERSIZED_ACCOUNT_KEY


def test_cleanup_iteration_visits_only_the_configured_budget():
    class CountingOrderedDict(OrderedDict):
        iterations = 0

        def __iter__(self):
            for key in super().__iter__():
                self.iterations += 1
                yield key

    clock = MutableClock()
    limiter = LoginAttemptLimiter(clock=clock, cleanup_budget=3)
    for index in range(20):
        assert limiter.allow(f"192.0.2.{index}", canonical_account_key(str(index)))
    counting = CountingOrderedDict(limiter._entries)
    limiter._entries = counting

    limiter.allow("198.51.100.1", canonical_account_key("next"))
    assert counting.iterations == 3


def test_concurrent_limiter_access_is_thread_safe():
    limiter = LoginAttemptLimiter(source_limit=20, source_account_limit=5)
    account = canonical_account_key("user@example.com")
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(lambda _: limiter.allow("192.0.2.1", account), range(100)))
    assert sum(results) == 5


def test_direct_peer_is_default_and_forwarding_headers_are_not_implicitly_trusted():
    resolver = SourceIdentityResolver()
    assert resolver.resolve("192.0.2.1", "198.51.100.10") == "192.0.2.1"
    assert resolver.resolve(None, "198.51.100.10") == "unknown"


def test_configured_proxy_selects_first_untrusted_address_from_the_right():
    resolver = SourceIdentityResolver.from_config(("10.0.0.0/8", "2001:db8:1::/48"))
    assert resolver.resolve("10.0.0.2", "198.51.100.4, 10.0.0.1") == "198.51.100.4"
    assert resolver.resolve("2001:db8:1::2", "2001:db8:2::4, 2001:db8:1::1") == "2001:db8:2::4"


def test_forwarding_from_untrusted_peer_and_malformed_trusted_chain_fall_back_to_peer():
    resolver = SourceIdentityResolver.from_config(("10.0.0.0/8",))
    assert resolver.resolve("192.0.2.1", "198.51.100.4") == "192.0.2.1"
    for malformed in ("", "unknown", "198.51.100.4,", "198.51.100.4:1234", "x" * 4097):
        assert resolver.resolve("10.0.0.2", malformed) == "10.0.0.2"


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_browser_ignores_arbitrary_forwarding_headers_by_default(header):
    limiter = LoginAttemptLimiter(source_account_limit=1)
    services, client = configured_web(limiter)
    services.authentication.authenticate = Mock(return_value=None)
    request_environment = {"environ_base": {"REMOTE_ADDR": "192.0.2.1"}}

    first = post_login(
        client,
        "admin@example.com",
        "wrong password",
        headers={header: "198.51.100.1"},
        **request_environment,
    )
    second = post_login(
        client,
        "admin@example.com",
        "wrong password",
        headers={header: "203.0.113.1"},
        **request_environment,
    )

    assert first.status_code == second.status_code == 401
    assert generic_login_failure_body(first) == generic_login_failure_body(second)
    assert services.authentication.authenticate.call_count == 1


def test_throttled_browser_request_skips_authentication_and_remains_generic():
    limiter = LoginAttemptLimiter(source_account_limit=1)
    admission = Mock(wraps=BrowserAuthenticationAdmissionGuard())
    services, client = configured_web(limiter, admission)
    wrapped = Mock(wraps=services.authentication.authenticate)
    services.authentication.authenticate = wrapped

    wrong = post_login(client, "admin@example.com", "wrong password")
    throttled = post_login(client, "admin@example.com", "wrong password")
    unknown = post_login(client, "unknown@example.com", "wrong password")

    assert wrong.status_code == throttled.status_code == unknown.status_code == 401
    assert (
        len({generic_login_failure_body(response) for response in (wrong, throttled, unknown)}) == 1
    )
    assert wrapped.call_count == 2
    assert admission.try_acquire.call_count == 2


def test_successful_browser_authentication_resets_pair_state():
    limiter = LoginAttemptLimiter(source_limit=20, source_account_limit=1)
    services, client = configured_web(limiter)
    wrapped = Mock(wraps=services.authentication.authenticate)
    services.authentication.authenticate = wrapped

    assert post_login(client, "admin@example.com", PASSWORD).status_code == 302
    assert post_login(client, " ADMIN@example.com ", PASSWORD).status_code == 302
    assert wrapped.call_count == 2


def test_oversized_browser_identifiers_are_generic_bounded_and_skip_authentication():
    limiter = LoginAttemptLimiter()
    services, client = configured_web(limiter)
    services.authentication.authenticate = Mock(return_value=None)

    for character in ("A", "B"):
        response = post_login(
            client,
            character * (MAX_BROWSER_LOGIN_IDENTIFIER_CODEPOINTS + 1),
            "wrong password",
        )
        assert response.status_code == 401
        generic_login_failure_body(response)

    assert services.authentication.authenticate.call_count == 0
    assert limiter.entry_count == 2


def test_multi_megabyte_login_body_is_rejected_before_form_buffering_or_authentication():
    limiter = LoginAttemptLimiter()
    services, client = configured_web(limiter)
    services.authentication.authenticate = Mock(return_value=None)
    token = csrf(client.get("/login", base_url=HTTPS))

    response = client.post(
        "/login",
        base_url=HTTPS,
        data={
            "email": "A" * 2_000_000,
            "password": "wrong password",
            "csrf_token": token,
        },
    )

    assert response.status_code == 401
    generic_login_failure_body(response)
    assert services.authentication.authenticate.call_count == 0
    assert limiter.entry_count == 0


def test_account_failure_states_have_the_same_browser_response():
    services, client = configured_web(LoginAttemptLimiter())
    admin = services.authentication.authenticate("admin@example.com", PASSWORD)
    inactive = services.users.create_user(admin.id, "inactive@example.com", "Inactive", PASSWORD)
    deleted = services.users.create_user(admin.id, "deleted@example.com", "Deleted", PASSWORD)
    services.users.disable_user(admin.id, inactive.id)
    services.users.delete_user(admin.id, deleted.id)
    responses = [
        post_login(client, email, "wrong password")
        for email in (
            "admin@example.com",
            "unknown@example.com",
            inactive.email,
            deleted.email,
        )
    ]
    assert {response.status_code for response in responses} == {401}
    # Only the user's submitted email and random CSRF token may differ.
    assert len({generic_login_failure_body(response) for response in responses}) == 1


def test_required_field_failures_still_consume_limiter_history():
    services, client = configured_web(LoginAttemptLimiter(source_account_limit=1))
    services.authentication.authenticate = Mock(return_value=None)
    assert post_login(client, "admin@example.com", "").status_code == 400
    assert post_login(client, "admin@example.com", PASSWORD).status_code == 401
    services.authentication.authenticate.assert_not_called()
