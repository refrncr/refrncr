"""Short-lived browser review identities, signed with a server-only application key.

No content or diff is stored. A restart intentionally requires a fresh review.
"""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import asdict

from refrncr.concept_contracts import DiscardIdentity
from refrncr.diff_contracts import ReviewIdentity


def encode_review(identity, session_id, secret):
    payload = json.dumps(
        [asdict(identity), session_id, secrets.token_hex(8)], separators=(",", ":")
    ).encode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.digest(secret, body, hashlib.sha256).hex().encode()
    return (body + b"." + signature).decode()


def decode_review(token, session_id, secret):
    return _decode(token, session_id, secret, ReviewIdentity)


def encode_discard(identity, session_id, secret):
    return encode_review(identity, session_id, hmac.digest(secret, b"concept-discard", "sha256"))


def decode_discard(token, session_id, secret):
    return _decode(
        token, session_id, hmac.digest(secret, b"concept-discard", "sha256"), DiscardIdentity
    )


def _decode(token, session_id, secret, identity_type):
    if not isinstance(token, str) or len(token) > 1500:
        raise ValueError("Invalid review")
    try:
        body, signature = token.encode("ascii").split(b".")
        if not hmac.compare_digest(
            signature, hmac.digest(secret, body, hashlib.sha256).hex().encode()
        ):
            raise ValueError
        identity, bound_session, _nonce = json.loads(
            base64.urlsafe_b64decode(body + b"=" * (-len(body) % 4))
        )
        if bound_session != session_id:
            raise ValueError
        return identity_type(**identity)
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Invalid review") from None
