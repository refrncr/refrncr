"""Canonical encoding, decoding, and validation of Record identifiers."""

from collections.abc import Iterable, Mapping

from refrncr.errors import ValidationError

MAX_RECORD_IDENTIFIER_BYTES = 2000
RECORD_IDENTIFIER_MESSAGES = {
    "too_long": (
        "Record identifier is too long: the identifier fields together may be at most "
        "2,000 bytes. Shorten one of them."
    ),
    "incomplete": "Record identifier is incomplete: every identifier field needs a value.",
}


class RecordIdentifierError(ValidationError):
    """An identifier is incomplete, malformed, or exceeds its storage limit."""

    def __init__(self, reason: str = "incomplete") -> None:
        if reason not in RECORD_IDENTIFIER_MESSAGES:
            reason = "incomplete"
        self.reason = reason
        self.code = reason
        super().__init__(RECORD_IDENTIFIER_MESSAGES[reason])


def encode_record_identifier(
    components: Mapping[str, bytes] | Iterable[tuple[str, bytes]],
    *,
    expected_keys: Iterable[str] | None = None,
) -> bytes:
    """Encode components in field-key order using the existing 4-byte prefix format."""
    items = list(components.items() if isinstance(components, Mapping) else components)
    if not items or any(not isinstance(key, str) or not key for key, _ in items):
        raise RecordIdentifierError
    if len({key for key, _ in items}) != len(items):
        raise RecordIdentifierError
    if expected_keys is not None:
        expected = tuple(expected_keys)
        if (
            not expected
            or len(set(expected)) != len(expected)
            or set(expected) != {key for key, _ in items}
        ):
            raise RecordIdentifierError
    if not all(isinstance(value, bytes) for _, value in items):
        raise RecordIdentifierError
    if sum(len(value) + 4 for _, value in items) > MAX_RECORD_IDENTIFIER_BYTES:
        raise RecordIdentifierError("too_long")

    encoded = bytearray()
    try:
        for _key, value in sorted(items, key=lambda item: item[0]):
            encoded.extend(len(value).to_bytes(4, "big"))
            encoded.extend(value)
            if len(encoded) > MAX_RECORD_IDENTIFIER_BYTES:
                raise RecordIdentifierError("too_long")
    except (OverflowError, TypeError):
        raise RecordIdentifierError from None
    return bytes(encoded)


def decode_record_identifier(encoded: bytes) -> tuple[bytes, ...]:
    """Decode and structurally validate a complete encoded identifier."""
    if not isinstance(encoded, bytes) or not encoded:
        raise RecordIdentifierError
    if len(encoded) > MAX_RECORD_IDENTIFIER_BYTES:
        raise RecordIdentifierError("too_long")
    components = []
    offset = 0
    while offset < len(encoded):
        if len(encoded) - offset < 4:
            raise RecordIdentifierError
        length = int.from_bytes(encoded[offset : offset + 4], "big")
        offset += 4
        end = offset + length
        if end > len(encoded):
            raise RecordIdentifierError
        components.append(encoded[offset:end])
        offset = end
    return tuple(components)
