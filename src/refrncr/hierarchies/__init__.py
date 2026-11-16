"""Hierarchy domain services, contracts, and import workflows."""

from importlib import import_module

from refrncr.hierarchies.contracts import (
    HierarchyNode,
    applicable,
    hierarchy_node_paths,
    hierarchy_order,
    hierarchy_path_label,
    hierarchy_paths,
    hierarchy_sibling_sort_key,
    validate_hierarchy_scope,
)
from refrncr.hierarchies.errors import (
    HierarchyConflictError,
    HierarchyError,
    HierarchyNotFoundError,
    HierarchyValidationError,
)

__all__ = [
    "HierarchyConflictError",
    "HierarchyError",
    "HierarchyImportService",
    "HierarchyNode",
    "HierarchyNotFoundError",
    "HierarchyService",
    "HierarchyValidationError",
    "MAX_HIERARCHY_NODE_NAME",
    "applicable",
    "canonical_hierarchy_view",
    "hierarchy_leaf_records",
    "hierarchy_node_paths",
    "hierarchy_order",
    "hierarchy_path_label",
    "hierarchy_paths",
    "hierarchy_sibling_sort_key",
    "hierarchy_snapshot",
    "hierarchy_validation_errors",
    "validate_hierarchy_scope",
]


def __getattr__(name: str):
    if name == "HierarchyImportService":
        return getattr(import_module("refrncr.hierarchies.imports"), name)
    if name in {
        "HierarchyService",
        "MAX_HIERARCHY_NODE_NAME",
        "canonical_hierarchy_view",
        "hierarchy_leaf_records",
        "hierarchy_snapshot",
        "hierarchy_validation_errors",
    }:
        return getattr(import_module("refrncr.hierarchies.service"), name)
    raise AttributeError(name)
