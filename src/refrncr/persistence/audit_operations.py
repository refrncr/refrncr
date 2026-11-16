from __future__ import annotations

import json
from functools import wraps
from types import SimpleNamespace

from sqlalchemy import (
    Text,
    case,
    cast,
    func,
    select,
    tuple_,
    update,
)
from sqlalchemy.exc import SQLAlchemyError

from refrncr.authorization import INSTALLATION_SETTINGS_ID
from refrncr.persistence.common import locked
from refrncr.persistence.models import (
    AuditEvent,
    Dataset,
    DatasetField,
    InstallationSettings,
    Team,
    User,
)
from refrncr.ports import (
    AuditObjectRecord,
    AuditReadUnavailable,
)


def _audit_read(method):
    @wraps(method)
    def read(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except SQLAlchemyError:
            raise AuditReadUnavailable("Audit history is temporarily unavailable") from None

    return read


class SqlAlchemyAuditReadOperations:
    """Bounded projection reads. No relationships, business content or write methods."""

    def __init__(self, session):
        self.session = session

    @staticmethod
    def _statement(*, detail=False):
        # Never load unbounded historical JSON. Lists only need the REF-141
        # deletion snapshot; details retain the same 4096-character ceiling.
        context = cast(AuditEvent.details, Text)
        details = (
            case((func.length(context) <= 4096, context), else_=None)
            if detail
            else case(
                (
                    (AuditEvent.object_type == "dataset")
                    & (
                        AuditEvent.action.in_(
                            (
                                "dataset.deleted",
                                "dataset.version_published",
                                "dataset.version_cancelled",
                            )
                        )
                    )
                    & (func.length(context) <= 4096),
                    context,
                ),
                else_=None,
            )
        )
        return select(
            AuditEvent.id,
            AuditEvent.timestamp,
            AuditEvent.actor_user_id,
            AuditEvent.object_type,
            AuditEvent.object_id,
            AuditEvent.action,
            details.label("details"),
        )

    @staticmethod
    def _record(row):
        values = dict(row._mapping)
        try:
            values["details"] = json.loads(values["details"]) if values["details"] else None
        except (ValueError, TypeError, RecursionError):
            values["details"] = None
        return SimpleNamespace(**values)

    @_audit_read
    def page(self, filters, *, before, ceiling, newer=False):
        statement = self._statement()
        if filters.start:
            statement = statement.where(AuditEvent.timestamp >= filters.start)
        if filters.end:
            statement = statement.where(AuditEvent.timestamp < filters.end)
        if filters.actor:
            statement = statement.where(AuditEvent.actor_user_id == filters.actor)
        if filters.object_type:
            statement = statement.where(AuditEvent.object_type == filters.object_type)
        if filters.action:
            kind, action = filters.action.split(":", 1)
            statement = statement.where(AuditEvent.object_type == kind, AuditEvent.action == action)
        ordering = tuple_(AuditEvent.timestamp, AuditEvent.id)
        if ceiling:
            statement = statement.where(ordering <= tuple_(ceiling.timestamp, ceiling.id))
        if before:
            marker = tuple_(before.timestamp, before.id)
            statement = statement.where(ordering > marker if newer else ordering < marker)
        columns = (AuditEvent.timestamp, AuditEvent.id)
        statement = statement.order_by(*(c.asc() if newer else c.desc() for c in columns)).limit(51)
        return tuple(self._record(row) for row in self.session.execute(statement))

    @_audit_read
    def get(self, event_id):
        row = self.session.execute(
            self._statement(detail=True).where(AuditEvent.id == event_id)
        ).first()
        return self._record(row) if row else None

    @_audit_read
    def actor_names(self, ids):
        if len(ids) > 51:
            raise ValueError("Audit actor lookup exceeds one page")
        return (
            dict(
                self.session.execute(
                    select(User.id, User.display_name).where(
                        User.id.in_(ids), User.is_deleted.is_(False)
                    )
                ).all()
            )
            if ids
            else {}
        )

    @_audit_read
    def resolve_objects(self, object_type, ids):
        if len(ids) > 50:
            raise ValueError("Audit object lookup exceeds one page")
        columns = {
            "user": (User.id, User.display_name),
            "team": (Team.id, Team.name),
            "dataset": (Dataset.id, Dataset.name),
            "dataset_field": (DatasetField.id, DatasetField.name),
        }.get(object_type)
        if columns is None:
            raise AuditReadUnavailable("Audit object resolution is unavailable")
        if not ids:
            return {}
        statement = select(*columns).where(columns[0].in_(ids))
        if object_type == "user":
            statement = statement.where(User.is_deleted.is_(False))
        names = dict(self.session.execute(statement).all())
        return {key: AuditObjectRecord(key in names, names.get(key)) for key in ids}


class SqlAlchemyInstallationSettingsOperations:
    def __init__(self, session):
        self.session = session

    def get_policy(self, *, for_update=False):
        row = self.session.scalar(
            locked(
                select(InstallationSettings).where(
                    InstallationSettings.id == INSTALLATION_SETTINGS_ID
                ),
                self.session,
                for_update,
            )
        )
        if row is None:
            raise RuntimeError("Installation settings are missing; run database migrations")
        return row.export_access_policy

    def change_policy(self, expected, policy):
        result = self.session.execute(
            update(InstallationSettings)
            .where(
                InstallationSettings.id == INSTALLATION_SETTINGS_ID,
                InstallationSettings.export_access_policy == expected,
            )
            .values(export_access_policy=policy)
        )
        return result.rowcount == 1
