"""Shared normalization for uploaded filenames."""

from pathlib import PurePath


def safe_upload_filename(filename: str | None) -> str:
    """Normalize upload names consistently across platform-specific path syntax."""
    name = PurePath((filename or "").replace("\\", "/")).name.strip()
    return name[:255] or "import"
