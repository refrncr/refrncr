from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from refrncr.identity.login_security import SourceIdentityResolver


class TrustedProxyMiddleware:
    """Recognize HTTPS only when an explicitly trusted direct proxy says so."""

    def __init__(self, app: ASGIApp, source_resolver: SourceIdentityResolver) -> None:
        self.app = app
        self.source_resolver = source_resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._is_trusted_https(scope):
            scope = {**scope, "scheme": "https"}
        await self.app(scope, receive, send)

    def _is_trusted_https(self, scope: Scope) -> bool:
        client = scope.get("client")
        direct_peer = client[0] if client is not None else None
        if not self.source_resolver.is_trusted_peer(direct_peer):
            return False
        values = [
            value
            for name, value in scope.get("headers", ())
            if name.lower() == b"x-forwarded-proto"
        ]
        return len(values) == 1 and values[0].strip().lower() == b"https"
