"""Operator-only REF-73 ownership preflight; never an application authorization path."""

import json
from pathlib import Path

from sqlalchemy import inspect, text


class DatasetUpgradeError(RuntimeError):
    """The operator must resolve Dataset ownership before upgrading."""


def read_owner_mapping(path):
    if path is None:
        return {}

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DatasetUpgradeError("Ownership mapping contains duplicate Dataset keys")
            result[key] = value
        return result

    try:
        mapping = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
    except (OSError, ValueError) as exc:
        raise DatasetUpgradeError("Cannot read ownership mapping as a JSON object") from exc
    if not isinstance(mapping, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()
    ):
        raise DatasetUpgradeError("Ownership mapping must map Dataset keys to existing team IDs")
    return mapping


def ownership_preflight(connection, mapping=None):
    """Return resolved IDs and every ambiguous Dataset, without writes.

    Mapping keys are stable Dataset keys; values are existing team UUIDs. Existing
    owner_team_id values are used only when inspecting an already upgraded schema.
    Operators must stop all writers before preflight/upgrade.
    """
    mapping = mapping or {}
    inspector = inspect(connection)
    if not inspector.has_table("datasets"):
        if mapping:
            raise DatasetUpgradeError("Ownership mapping references unknown Datasets")
        return {}, []
    teams = dict(connection.execute(text("SELECT id, name FROM teams")).all())
    columns = {column["name"] for column in inspector.get_columns("datasets")}
    owner_column = ", owner_team_id" if "owner_team_id" in columns else ""
    rows = (
        connection.execute(
            text(f"SELECT id, key, owner_user_id{owner_column} FROM datasets ORDER BY key")
        )
        .mappings()
        .all()
    )
    if set(mapping) - {row["key"] for row in rows}:
        raise DatasetUpgradeError("Ownership mapping references unknown Datasets")
    if any(team_id not in teams for team_id in mapping.values()):
        raise DatasetUpgradeError("Ownership mapping references unavailable teams")
    memberships = {}
    for user_id, team_id in connection.execute(
        text("SELECT m.user_id, m.team_id FROM memberships m JOIN teams t ON t.id=m.team_id")
    ):
        memberships.setdefault(user_id, set()).add(team_id)
    resolved, unresolved = {}, []
    for row in rows:
        candidates = memberships.get(row["owner_user_id"], set())
        chosen = mapping.get(row["key"]) or row.get("owner_team_id")
        if chosen is None and len(candidates) == 1:
            chosen = next(iter(candidates))
        if chosen is not None and chosen in teams:
            resolved[row["id"]] = chosen
        else:
            unresolved.append(
                {
                    "key": row["key"],
                    "creator_user_id": row["owner_user_id"],
                    "candidate_team_ids": sorted(candidates),
                }
            )
    return resolved, unresolved


def require_resolved_ownership(connection, mapping=None):
    resolved, unresolved = ownership_preflight(connection, mapping)
    if unresolved:
        keys = ", ".join(row["key"] for row in unresolved)
        raise DatasetUpgradeError(
            f"REF-73 needs explicit owning teams for: {keys}. Stop application writers; run "
            "refrncr dataset-ownership-preflight, supply --mapping, then --upgrade."
        )
    return resolved
