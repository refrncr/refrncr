"""Persistence-neutral port contracts."""

from __future__ import annotations

from typing import Protocol

from .datasets import RecordValue


class HierarchyNode(Protocol):
    """Structural node shape used by persistence ports without a domain import."""

    id: str
    dataset_id: str
    name: str
    parent_id: str | None
    values: tuple[RecordValue, ...]
    revision: int


class HierarchyOperations(Protocol):
    def list_for_dataset(self, dataset_id: str) -> tuple[HierarchyNode, ...]: ...
    def get(self, dataset_id: str, node_id: str) -> HierarchyNode | None: ...
    def create(self, dataset_id: str, name: str, parent_id: str | None) -> HierarchyNode: ...
    def update(
        self, dataset_id: str, node_id: str, name: str, parent_id: str | None
    ) -> HierarchyNode: ...
    def replace_values(self, dataset_id: str, node_id: str, values) -> HierarchyNode: ...
    def delete(self, dataset_id: str, node_id: str) -> None: ...
    def field_has_values(self, dataset_id: str, field_id: str) -> bool: ...
