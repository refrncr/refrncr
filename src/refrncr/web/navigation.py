"""Shared browser navigation state."""

from flask import request


def active_navigation() -> str:
    """Return the visible navigation item for the current endpoint."""
    endpoint = request.endpoint or ""
    if endpoint.startswith("settings.api_key_"):
        return "api_keys"
    if endpoint in {"settings.appearance_settings", "settings.theme_preference"}:
        return "appearance"
    if endpoint == "settings.dataset_access_settings":
        return "dataset_access"
    if endpoint.startswith("audit."):
        return "audit"
    if endpoint == "overview.index":
        return "home"
    if endpoint.startswith("users."):
        return "users"
    if endpoint.startswith("teams."):
        return "teams"
    return ""
