"""Bounded UI metadata and the shared name-to-key canonicalization rule."""

import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_FIELD_DEFINITION = 4000
MAX_FIELD_UNIT = 64

# The pinned local sprite is the source of truth for available names. No user
# input is used in file paths or parsed as SVG. The UI offers a small suggestion set.
DEFAULT_ICON_SPRITE = "images/lucide-1.46.0.svg"
LEGACY_ICON_SPRITE = "images/lucide-legacy-0.468.0.svg"
# Stable icon names alias only symbols in these bundled, trusted files. Some retired
# symbols (including brand icons) have no equivalent in the new Lucide release.
ICON_SPRITES = {
    symbol.attrib["id"]: sprite
    for sprite in (LEGACY_ICON_SPRITE, DEFAULT_ICON_SPRITE)
    for symbol in ET.parse(Path(__file__).parent / "static" / sprite)
    .getroot()
    .findall(".//{http://www.w3.org/2000/svg}symbol")
}
LUCIDE_ICON_NAMES = frozenset(ICON_SPRITES)


def rendered_dataset_icon(value):
    return (
        value
        if isinstance(value, str) and len(value) <= 64 and value in LUCIDE_ICON_NAMES
        else "table"
    )


def dataset_icon_sprite(value):
    return ICON_SPRITES[rendered_dataset_icon(value)]


# Product-owner selection (REF-165); every name resolves in the pinned 1.46.0 sprite.
DATASET_ICONS = {
    "landmark": "Landmark",
    "banknote": "Banknote",
    "languages": "Languages",
    "earth": "Earth",
    "atom": "Atom",
    "flask-conical": "Flask conical",
    "test-tube-diagonal": "Test tube",
    "tag": "Tag",
    "bookmark": "Bookmark",
    "signpost-big": "Signpost",
    "building": "Building",
    "layers": "Layers",
    "vector-polygon": "Vector polygon",
    "paint-bucket": "Paint bucket",
    "bot": "Bot",
    "network": "Network",
    "database": "Database",
    "scale": "Scale",
    "file-box": "File box",
    "target": "Target",
    "ruler-dimension-line": "Ruler dimension",
    "gallery-vertical-end": "Gallery vertical",
    "clock": "Clock",
    "vector-square": "Vector square",
    "settings": "Settings",
    "file-sliders": "File sliders",
    "hand-coins": "Hand coins",
    "credit-card": "Credit card",
    "building-complex": "Building complex",
    "users-round": "Users round",
    "user-round": "User round",
    "wrench": "Wrench",
    "refresh-ccw": "Refresh",
    "zap": "Zap",
    "cable": "Cable",
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
