"""Framework-independent authorization value objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthorizedResourceSet:
    all_resources: bool
    resource_ids: tuple[str, ...]
