import pytest

from refrncr.record_identity import (
    MAX_RECORD_IDENTIFIER_BYTES,
    RECORD_IDENTIFIER_ERROR,
    RecordIdentifierError,
    decode_record_identifier,
    encode_record_identifier,
    validate_record_identifier,
)


def test_identifier_vector_is_sorted_and_byte_compatible():
    components = {"z-last": b"\xc3\xa9", "a-empty": b"", "m": b"\x01"}
    expected = b"\x00\x00\x00\x00\x00\x00\x00\x01\x01\x00\x00\x00\x02\xc3\xa9"

    assert encode_record_identifier(components) == expected
    assert encode_record_identifier(tuple(reversed(tuple(components.items())))) == expected
    assert decode_record_identifier(expected) == (b"", b"\x01", b"\xc3\xa9")
    assert validate_record_identifier(expected, expected_component_count=3) == (
        b"",
        b"\x01",
        b"\xc3\xa9",
    )


def test_identifier_boundary_and_oversize_share_one_domain_error():
    at_limit = {"id": b"x" * (MAX_RECORD_IDENTIFIER_BYTES - 4)}
    assert len(encode_record_identifier(at_limit)) == MAX_RECORD_IDENTIFIER_BYTES

    with pytest.raises(RecordIdentifierError, match=f"^{RECORD_IDENTIFIER_ERROR}$"):
        encode_record_identifier({"id": b"x" * (MAX_RECORD_IDENTIFIER_BYTES - 3)})


@pytest.mark.parametrize("components", [{}, (("id", b"ok"), ("id", b"duplicate"))])
def test_incomplete_or_duplicate_components_use_the_shared_error(components):
    with pytest.raises(RecordIdentifierError, match=f"^{RECORD_IDENTIFIER_ERROR}$"):
        encode_record_identifier(components)


def test_missing_expected_component_uses_the_shared_error():
    with pytest.raises(RecordIdentifierError, match=f"^{RECORD_IDENTIFIER_ERROR}$"):
        encode_record_identifier({"id": b"x"}, expected_keys=("id", "type"))


@pytest.mark.parametrize("encoded", [b"", b"\x00", b"\x00\x00\x00\x02x"])
def test_malformed_identifier_uses_the_shared_error(encoded):
    with pytest.raises(RecordIdentifierError, match=f"^{RECORD_IDENTIFIER_ERROR}$"):
        decode_record_identifier(encoded)


def test_wrong_component_count_uses_the_shared_error():
    encoded = encode_record_identifier({"id": b"x"})
    with pytest.raises(RecordIdentifierError, match=f"^{RECORD_IDENTIFIER_ERROR}$"):
        validate_record_identifier(encoded, expected_component_count=2)
