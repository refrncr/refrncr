"""Bounded UI metadata and the shared name-to-key canonicalization rule."""

import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_FIELD_DEFINITION = 4000
MAX_FIELD_UNIT = 64

# The pinned local sprite is the source of truth for available names. No user
# input is used in file paths or parsed as SVG. The UI offers a small suggestion set.
LUCIDE_ICON_NAMES = frozenset(
    symbol.attrib["id"]
    for symbol in ET.parse(Path(__file__).parent / "static/images/lucide-0.468.0.svg")
    .getroot()
    .findall(".//{http://www.w3.org/2000/svg}symbol")
)


def rendered_dataset_icon(value):
    return (
        value
        if isinstance(value, str) and len(value) <= 64 and value in LUCIDE_ICON_NAMES
        else "table"
    )


DATASET_ICONS = {
    "house": "House",
    "key-round": "Key round",
    "lock": "Lock",
    "scroll-text": "Scroll text",
    "settings-2": "Settings",
    "table": "Table",
    "user": "User",
    "users": "Users",
}


def key_from_name(name: str) -> str:
    """NFKD, lowercase, ASCII separators, ASCII alphanumerics, collapsed underscores.

    Separator set is explicit (and identical in key-generation.js). Other non-ASCII
    characters that do not decompose to ASCII are removed, not transliterated.
    Never truncate or append a uniqueness suffix; normal key validation is final.
    """
    value = unicodedata.normalize("NFKD", name).lower()
    value = re.sub(
        r"[\t\n\v\f\r /\\.\-\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\u2010-\u2015]+",
        "_",
        value,
    )
    value = re.sub(r"[^a-z0-9_]", "", value)
    return re.sub(r"_+", "_", value).strip("_")


def optional_plain_text(value: str | None, limit: int, label: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{label} must be text of at most {limit} characters")
    if "\x00" in value:
        raise ValueError(f"{label} cannot contain null characters")
    return value


def dataset_icon(value: str | None) -> str | None:
    """Persist only bounded, normalized catalog names; all other input means default.

    Bound before normalization/lookup. Never truncate into a different valid name,
    retain raw input, interpret markup, or construct a path from the submitted value.
    """
    if not isinstance(value, str) or len(value) > 64:
        return None
    normalized = value.strip().lower()
    return normalized if normalized in LUCIDE_ICON_NAMES else None
