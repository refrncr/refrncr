"""Canonical encoding, decoding, and validation of Record identifiers."""

from collections.abc import Iterable, Mapping

from refrncr.errors import ValidationError

MAX_RECORD_IDENTIFIER_BYTES = 2000
RECORD_IDENTIFIER_ERROR = "Record identifier must be complete and at most 2000 bytes"


class RecordIdentifierError(ValidationError):
    """An identifier is incomplete, malformed, or exceeds its storage limit."""

    def __init__(self) -> None:
        super().__init__(RECORD_IDENTIFIER_ERROR)


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

    encoded = bytearray()
    try:
        for _key, value in sorted(items, key=lambda item: item[0]):
            if not isinstance(value, bytes):
                raise RecordIdentifierError
            encoded.extend(len(value).to_bytes(4, "big"))
            encoded.extend(value)
            if len(encoded) > MAX_RECORD_IDENTIFIER_BYTES:
                raise RecordIdentifierError
    except (OverflowError, TypeError):
        raise RecordIdentifierError from None
    return bytes(encoded)


def decode_record_identifier(encoded: bytes) -> tuple[bytes, ...]:
    """Decode and structurally validate a complete encoded identifier."""
    if not isinstance(encoded, bytes) or not encoded or len(encoded) > MAX_RECORD_IDENTIFIER_BYTES:
        raise RecordIdentifierError
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


def validate_record_identifier(
    encoded: bytes, *, expected_component_count: int | None = None
) -> tuple[bytes, ...]:
    """Validate the encoded structure and, when known, its required component count."""
    components = decode_record_identifier(encoded)
    if expected_component_count is not None and (
        expected_component_count < 1 or len(components) != expected_component_count
    ):
        raise RecordIdentifierError
    return components
