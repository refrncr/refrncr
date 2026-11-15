from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from refrncr import errors as _errors
from refrncr.audit import record_audit
from refrncr.authorization import (
    AuthorizationService as AuthorizationService,
)
from refrncr.authorization import (
    AuthorizedResourceSet as AuthorizedResourceSet,
)
from refrncr.authorization import (
    DatasetAuthorizationService as DatasetAuthorizationService,
)
from refrncr.authorization import (
    DatasetResourceResolver as DatasetResourceResolver,
)
from refrncr.authorization import GrantService as GrantService
from refrncr.authorization import ResourceTypeRegistry as ResourceTypeRegistry
from refrncr.authorization import (
    create_deprecated_grant_service as create_deprecated_grant_service,
)
from refrncr.datatypes import DATASET_FIELD_TYPES as DATASET_FIELD_TYPES
from refrncr.datatypes import DatatypeError, canonicalize, get_datatype
from refrncr.domain import advance_concept, require_concept, valid_identity
from refrncr.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    StaleDatasetOwnerError,
    StaleFieldOrderError,
    StaleRecordError,
    ValidationError,
)
from refrncr.field_contracts import FIELD_KEY_PATTERN
from refrncr.identity import (
    AuthenticatedPrincipal as AuthenticatedPrincipal,
)
from refrncr.identity import (
    AuthenticationService as AuthenticationService,
)
from refrncr.identity import (
    IdentityAuthorizationError as IdentityAuthorizationError,
)
from refrncr.identity import (
    IdentityConflictError as IdentityConflictError,
)
from refrncr.identity import (
    IdentityError as IdentityError,
)
from refrncr.identity import (
    IdentityNotFoundError as IdentityNotFoundError,
)
from refrncr.identity import (
    IdentityValidationError as IdentityValidationError,
)
from refrncr.identity import (
    InvalidLifecycleError as InvalidLifecycleError,
)
from refrncr.identity import (
    MembershipService as MembershipService,
)
from refrncr.identity import (
    SessionService as SessionService,
)
from refrncr.identity import (
    TeamService as TeamService,
)
from refrncr.identity import (
    UserService as UserService,
)
from refrncr.identity import (
    normalize_email as normalize_email,
)
from refrncr.identity import (
    normalize_team_name as normalize_team_name,
)
from refrncr.identity import (
    require_admin as require_admin,
)
from refrncr.identity import (
    validate_password as validate_password,
)
from refrncr.identity.shared import PASSWORD_HASHER as PASSWORD_HASHER
from refrncr.ports import (
    AuditEntry,
    DatasetBusinessKey,
    DatasetConfiguration,
    DatasetFieldRecord,
    DatasetOverview,
    DatasetRecord,
    PersistenceConflictError,
    RecordValue,
    StoredRecord,
    UnitOfWork,
    UnitOfWorkFactory,
)
from refrncr.presentation_metadata import (
    MAX_FIELD_DEFINITION,
    MAX_FIELD_UNIT,
    dataset_icon,
    optional_plain_text,
)
from refrncr.record_identity import encode_record_identifier
from refrncr.version_contracts import mapping_incomplete_message

SUBJECT_TYPES = {"user", "team"}

# Compatibility facade while services move to domain packages. These assignments keep
# existing imports working without making ``errors.py`` depend on the facade.
DatasetError = _errors.DatasetError
DomainError = _errors.DomainError
HierarchyError = _errors.HierarchyError
ImportDomainError = _errors.ImportDomainError
PersistenceError = _errors.PersistenceError
AccessManagementError = _errors.AccessManagementError
UserManagementError = _errors.UserManagementError
VersionError = _errors.VersionError

_EXPECTED_REVISION_PATTERN = re.compile(r"[1-9][0-9]*")
MAX_RECORD_REVISION = 2_147_483_647


def parse_expected_revision(raw: str) -> int:
    if not isinstance(raw, str) or _EXPECTED_REVISION_PATTERN.fullmatch(raw) is None:
        raise ValidationError("Expected revision must be a canonical positive integer")
    revision = int(raw)
    if revision > MAX_RECORD_REVISION:
        raise ValidationError("Expected revision is out of range")
    return revision


@dataclass(frozen=True, slots=True)
class DatasetDetails:
    id: str
    key: str
    name: str
    description: str
    owner_team_id: str
    owner_team_name: str
    record_count: int
    last_updated: datetime


@dataclass(frozen=True, slots=True)
class RecordPage:
    records: tuple[StoredRecord, ...]
    page: int
    page_size: int
    total: int


def validate_dataset_key(key: str) -> str:
    if not isinstance(key, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", key) is None:
        raise ValidationError(
            "Dataset key must contain 1-100 lowercase ASCII letters, numbers, "
            "underscores, or hyphens",
            field="key",
        )
    return key


def normalize_dataset_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValidationError("Dataset name must be text", field="name")
    normalized_name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(normalized_name) <= 200:
        raise ValidationError(
            "Dataset name must contain between 1 and 200 characters", field="name"
        )
    return normalized_name


def validate_dataset_description(description: str) -> str:
    if not isinstance(description, str):
        raise ValidationError("Dataset description must be text", field="description")
    if len(description) > 10_000:
        raise ValidationError(
            "Dataset description must not exceed 10000 characters", field="description"
        )
    return description


def validate_dataset_field_key(key: str) -> str:
    if not isinstance(key, str) or re.fullmatch(FIELD_KEY_PATTERN, key) is None:
        raise ValidationError(
            "Field key must contain 1-100 lowercase ASCII letters, numbers, or underscores"
        )
    return key


def normalize_dataset_field_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValidationError("Field name must be text")
    normalized_name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(normalized_name) <= 200:
        raise ValidationError("Field name must contain between 1 and 200 characters")
    return normalized_name


def validate_field_metadata(definition, unit):
    try:
        return (
            optional_plain_text(definition, MAX_FIELD_DEFINITION, "Definition"),
            optional_plain_text(unit, MAX_FIELD_UNIT, "Unit"),
        )
    except ValueError as error:
        raise ValidationError(str(error)) from None


def validate_dataset_icon(value):
    try:
        return dataset_icon(value)
    except ValueError as error:
        raise ValidationError(str(error)) from None


def validate_dataset_field_type(data_type: str) -> str:
    try:
        return get_datatype(data_type).id
    except DatatypeError as error:
        raise ValidationError(str(error)) from None


def _require_identifier_type(data_type: str) -> None:
    validate_dataset_field_type(data_type)
    if not get_datatype(data_type).identifier_eligible:
        raise ValidationError("Float cannot be part of a Record identifier")


def validate_dataset_field_flag(value: bool, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{label} must be a boolean")
    return value


def canonicalize_record_value(data_type: str, raw: str | None) -> tuple[object, bytes] | None:
    try:
        return canonicalize(data_type, raw)
    except DatatypeError as error:
        raise ValidationError(str(error)) from None


@dataclass(frozen=True, slots=True)
class DatasetDeletion:
    id: str
    key: str
    name: str
    field_count: int
    record_count: int
    version_count: int
    export_access_count: int
    expected_updated_at: str


def _version_reads(service):
    from refrncr.dataset_versions import DatasetVersionService

    return service.versions or DatasetVersionService(service.uow, service.authorization.datasets)


def _mapping_deletion_error(count):
    if count:
        dependency = "1 mapping is" if count == 1 else f"{count} mappings are"
        return f"This dataset can't be deleted because {dependency} based on it."
    return None


class DatasetService:
    """Authorized Dataset operations within the one Community installation context."""

    def __init__(
        self, uow: UnitOfWorkFactory, authorization: AuthorizationService, *, versions=None
    ) -> None:
        self.uow = uow
        self.authorization = authorization
        self.versions = versions

    def get_dataset_for_deletion(self, actor_id: str, dataset_id: str) -> DatasetDeletion:
        with self.uow(translate_errors=True) as uow:
            uow.begin_export_snapshot()
            dataset = self._deletion_target(uow, actor_id, dataset_id)
            return self._deletion_context(uow, dataset)

    def delete_dataset(
        self, actor_id: str, dataset_id: str, confirmation_name: str, expected_updated_at: str
    ) -> DatasetDeletion:
        with self.uow(write=True, serialized=True, translate_errors=True) as uow:
            dataset = self._deletion_target(uow, actor_id, dataset_id, for_update=True)
            if expected_updated_at != self._deletion_timestamp(dataset):
                raise ConflictError("Dataset changed. Reopen the confirmation and try again.")
            # Deliberately no strip, normalization, locale or casefold comparison.
            if not isinstance(confirmation_name, str) or confirmation_name != dataset.name:
                raise ValidationError("Enter the current dataset name exactly to confirm deletion.")
            result = self._deletion_context(uow, dataset)
            uow.datasets.delete_with_dependents(dataset.id)
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset",
                object_id=result.id,
                action="dataset.deleted",
                details={"dataset_name": result.name},
            )
            uow.flush()
            return result

    def _deletion_target(self, uow, actor_id, dataset_id, *, for_update=False):
        if not valid_identity(actor_id) or not valid_identity(dataset_id):
            raise NotFoundError("Dataset not found")
        dataset = uow.datasets.get(dataset_id, for_update=for_update)
        if dataset is None or not self.authorization.datasets.can_maintain_dataset(
            actor_id, dataset.id, uow=uow
        ):
            raise NotFoundError("Dataset not found")
        if error := _mapping_deletion_error(uow.datasets.mapping_usage_count(dataset.id)):
            raise ConflictError(error)
        return dataset

    @staticmethod
    def _deletion_timestamp(dataset):
        return dataset.updated_at.isoformat(timespec="microseconds")

    def _deletion_context(self, uow, dataset):
        fields, records, versions, export_access = uow.datasets.deletion_counts(dataset.id)
        return DatasetDeletion(
            dataset.id,
            dataset.key,
            dataset.name,
            fields,
            records,
            versions,
            export_access,
            self._deletion_timestamp(dataset),
        )

    def mapping_usage(self, actor_id, key, *, details=True):
        """Dataset-wide dependants, with private version details removed before presentation."""
        from refrncr.version_contracts import version_states

        with self.uow() as uow:
            uow.begin_export_snapshot()
            source = uow.datasets.find_by_key(self._validated_lookup_key(key))
            if source is None or not self.authorization.has_capability(
                actor_id, "can_view_dataset", source.id, uow=uow
            ):
                raise NotFoundError("Dataset not found")
            result = {"key": source.key, "total": 0, "mappings": [], "restricted": []}
            if not details:
                result["total"] = uow.datasets.mapping_usage_count(source.id)
                return result
            mappings = uow.datasets.mappings_using(source.id)
            result["total"] = len(mappings)
            result["deletion_error"] = _mapping_deletion_error(len(mappings))
            if not mappings:
                return result
            visible, maintainable, restricted = [], set(), {}
            for mapping in mappings:
                if not self.authorization.has_capability(
                    actor_id, "can_view_dataset", mapping.id, uow=uow
                ):
                    group = restricted.setdefault(
                        mapping.owner_team_id, {"team": mapping.owner_team_name, "count": 0}
                    )
                    group["count"] += 1
                    continue
                visible.append(mapping)
                if self.authorization.has_capability(
                    actor_id, "can_maintain_dataset", mapping.id, uow=uow
                ):
                    maintainable.add(mapping.id)
            publications = uow.versions.list_for_datasets(tuple(m.id for m in visible))
            concepts = uow.concepts.list_for_datasets(tuple(maintainable))
            sources = {v.id: v for v in uow.versions.list_for_dataset(source.id)}
            latest = max((v.number for v in sources.values() if v.cancelled_at is None), default=0)
            today = _version_reads(self).clock.today()
            for mapping in visible:
                mapping_versions = publications.get(mapping.id, ())
                # Match get_dataset_configuration: a never-published mapping's name
                # and existence are private to maintainers, even with dataset read access.
                if mapping.id not in maintainable and not any(
                    v.cancelled_at is None for v in mapping_versions
                ):
                    group = restricted.setdefault(
                        mapping.owner_team_id, {"team": mapping.owner_team_name, "count": 0}
                    )
                    group["count"] += 1
                    continue
                states = version_states(mapping_versions, today)
                permitted = (
                    ("Current", "Scheduled", "Expired")
                    if mapping.id in maintainable
                    else ("Current", "Expired")
                )
                state = next(
                    (s for status in permitted for s in states if s.status == status), None
                )
                draft = sources.get(mapping.source_version_id) if mapping.id in concepts else None
                pinned = sources.get(state.version.source_version_id) if state else draft
                result["mappings"].append(
                    {
                        "name": mapping.name,
                        "key": mapping.key,
                        "team": mapping.owner_team_name,
                        "version_id": state.version.id if state else None,
                        "status": state.status if state else "Draft" if draft else None,
                        "not_yet_in_effect": state is None
                        and any(s.status == "Scheduled" for s in states),
                        "source_number": pinned.number if pinned else None,
                        "update_available": bool(pinned and latest > pinned.number),
                        "draft_source_number": draft.number
                        if state and draft and draft.id != state.version.source_version_id
                        else None,
                    }
                )
            result["mappings"].sort(key=lambda m: (m["name"].casefold(), m["key"]))
            result["restricted"] = sorted(restricted.values(), key=lambda g: g["team"].casefold())
            return result

    def mapping_source(self, actor_id, key, version_id=None, *, history_version_ids=()):
        """Source metadata for an authorized mapping; never expose source drafts."""
        with self.uow() as uow:
            uow.begin_export_snapshot()
            dataset = uow.datasets.find_by_key(self._validated_lookup_key(key))
            if dataset is None or not self.authorization.has_capability(
                actor_id, "can_view_dataset", dataset.id, uow=uow
            ):
                raise NotFoundError("Dataset not found")
            if dataset.kind != "mapping":
                return None
            maintain = self.authorization.has_capability(
                actor_id, "can_maintain_dataset", dataset.id, uow=uow
            )
            source_id = dataset.source_version_id
            if not version_id and not maintain:
                raise NotFoundError("Dataset version not found")
            if version_id:
                version = uow.versions.get(dataset.id, version_id)
                if (
                    version is None
                    or version.cancelled_at is not None
                    or (
                        version.effective_date > _version_reads(self).clock.today() and not maintain
                    )
                ):
                    raise NotFoundError("Dataset version not found")
                source_id = version.source_version_id
            source = uow.datasets.get(dataset.source_dataset_id)
            history_sources = {}
            if history_version_ids:
                mapping_versions = {
                    version.id: version for version in uow.versions.list_for_dataset(dataset.id)
                }
                source_versions = {
                    version.id: version for version in uow.versions.list_for_dataset(source.id)
                }
                for requested_id in history_version_ids:
                    published = mapping_versions.get(requested_id)
                    if published is None or (
                        not maintain
                        and (
                            published.cancelled_at is not None
                            or published.effective_date > _version_reads(self).clock.today()
                        )
                    ):
                        raise NotFoundError("Dataset version not found")
                    pinned = source_versions.get(published.source_version_id)
                    if pinned is None:
                        raise NotFoundError("Source version not found")
                    history_sources[requested_id] = {
                        "name": pinned.name,
                        "number": pinned.number,
                        "version_id": pinned.id,
                    }
                version = source_versions.get(source_id)
                latest = next((v for v in source_versions.values() if v.cancelled_at is None), None)
            else:
                version = uow.versions.get(source.id, source_id)
                latest = uow.versions.latest_non_cancelled(source.id)
            visible = self.authorization.has_capability(
                actor_id, "can_view_dataset", source.id, uow=uow
            )
            from refrncr.mapping_updates import source_update_blocker

            source_fields = uow.versions.fields(version)
            concept = uow.concepts.get(dataset.id) if maintain else None
            draft_source = (
                uow.versions.get(source.id, dataset.source_version_id) if concept else None
            )
            already_updated = bool(
                version_id and draft_source and latest and draft_source.id == latest.id
            )
            update_blocker = None
            if latest and latest.number > version.number and not already_updated:
                identifier_type = next(
                    field.data_type
                    for field in source_fields
                    if field.key == dataset.source_field_key
                )
                update_blocker = source_update_blocker(
                    dataset, latest, uow.versions.fields(latest), identifier_type
                )
            draft = None
            if not version_id and concept:
                from refrncr.mapping_updates import mapping_states

                fields = uow.dataset_fields.list_for_dataset(dataset.id)
                states = mapping_states(
                    fields,
                    uow.dataset_records.iterate_all(dataset.id),
                    (record for record, _ in uow.dataset_records.deleted_records(dataset.id)),
                )
                baseline = (
                    uow.versions.get(dataset.id, concept.base_version_id)
                    if concept.base_version_id
                    else None
                )
                old_source = (
                    uow.versions.get(source.id, baseline.source_version_id) if baseline else None
                )
                draft = {
                    "notes": concept.source_update_notes or [],
                    "unmapped": sum(value == "Unmapped" for value in states.values()),
                    "removed": sum(value == "Removed from source" for value in states.values()),
                    "required_target": any(
                        field.is_required and field.mapping_role == "mapping_target"
                        for field in fields
                    ),
                    "previous_number": old_source.number
                    if old_source and old_source.id != version.id
                    else None,
                }
            current = uow.versions.current_for_datasets(
                (source.id,), _version_reads(self).clock.today()
            ).get(source.id)
            return {
                "effective_date": version.effective_date,
                "latest_effective_date": latest.effective_date if latest else None,
                "current_number": current.number
                if current and current.number > version.number
                else None,
                "current_effective_date": current.effective_date if current else None,
                "may_update": maintain,
                "update_blocker": update_blocker,
                "draft_already_updated": already_updated,
                "revision": dataset.revision,
                "draft": draft,
                "name": version.name,
                "key": source.key if visible else None,
                "number": version.number,
                "version_id": version.id,
                "latest_number": latest.number
                if latest and latest.number > version.number
                else None,
                "fields": source_fields,
                "history": history_sources,
            }

    def mapping_source_choices(self, actor_id):
        """Current reference snapshots only, including their published field definitions."""
        contexts = _version_reads(self).overview(actor_id)
        with self.uow() as uow:
            uow.begin_export_snapshot()
            ids = tuple(
                context.dataset.id
                for context in contexts
                if context.dataset.kind == "reference" and not context.no_current
            )
            versions = uow.versions.current_for_datasets(ids, _version_reads(self).clock.today())
            tables = tuple(
                context.dataset for context in contexts if context.dataset.id in versions
            )
            choices = {}
            for table in tables:
                fields = uow.versions.fields(versions[table.id])
                identifier_count = sum(field.is_identifier for field in fields)
                choices[table.id] = tuple(
                    field
                    for field in fields
                    if (field.is_unique or (field.is_identifier and identifier_count == 1))
                    and field.is_required
                    and get_datatype(field.data_type).identifier_eligible
                )
            return tables, choices

    def create_dataset(
        self,
        actor_id: str,
        key: str,
        name: str,
        description: str,
        owner_team_id: str | None = None,
        *,
        icon: str | None = None,
        kind: str = "reference",
        mapping_mode: str | None = None,
        source_system: str | None = None,
        target_system: str | None = None,
        source_dataset_id: str | None = None,
        target_dataset_id: str | None = None,
        source_field_key: str | None = None,
        target_field_key: str | None = None,
    ) -> DatasetRecord:
        with self.uow(write=True, serialized=True) as uow:
            if not self.authorization.datasets.can_create_dataset(actor_id, uow=uow):
                raise AuthorizationError("Active user access required")
            if owner_team_id is None:
                eligible = self.authorization.datasets.creation_teams(actor_id, uow=uow)
                if len(eligible) != 1:
                    raise ValidationError("Choose an owning team. Team membership is required.")
                owner_team_id = eligible[0].id
            if not self.authorization.datasets.can_create_dataset(actor_id, owner_team_id, uow=uow):
                if not self.authorization.datasets.active(uow.users.get(actor_id)):
                    raise AuthorizationError("Active user access required")
                raise ValidationError(
                    "Choose an available owning team you may create datasets for."
                )
            kind, mapping_mode, source_system, target_system = self._mapping_metadata(
                kind, mapping_mode, source_system, target_system
            )
            source_version = None
            if kind == "mapping" and mapping_mode == "linked":
                if not source_dataset_id:
                    raise ValidationError("Choose a source dataset for a mapping dataset.")
                linked = uow.datasets.get(source_dataset_id)
                visible = linked is not None and self.authorization.has_capability(
                    actor_id, "can_view_dataset", linked.id, uow=uow
                )
                if not visible or linked.kind != "reference":
                    raise ValidationError("Choose an available reference source dataset.")
                source_version = uow.versions.current_for_datasets(
                    (linked.id,), _version_reads(self).clock.today()
                ).get(linked.id)
                if source_version is None:
                    raise ValidationError(
                        f"Source dataset {linked.name} must have a Current version."
                    )
                source_fields = {field.key: field for field in uow.versions.fields(source_version)}
                if not source_field_key or source_field_key not in source_fields:
                    raise ValidationError("Choose an available source identifier.")
                source_field = source_fields[source_field_key]
                if (
                    not (
                        source_field.is_unique
                        or (
                            source_field.is_identifier
                            and sum(field.is_identifier for field in source_fields.values()) == 1
                        )
                    )
                    or not source_field.is_required
                    or not get_datatype(source_field.data_type).identifier_eligible
                ):
                    raise ValidationError("Choose a required, unique source identifier.")
                source_system = source_field.name
                target_system = target_dataset_id = target_field_key = None
            else:
                source_dataset_id = target_dataset_id = None
                source_field_key = target_field_key = None
            dataset = uow.datasets.create(
                key=validate_dataset_key(key),
                name=normalize_dataset_name(name),
                description=validate_dataset_description(description),
                owner_user_id=actor_id,
                owner_team_id=owner_team_id,
                icon=validate_dataset_icon(icon),
                kind=kind,
                mapping_mode=mapping_mode,
                source_system=source_system,
                target_system=target_system,
                source_dataset_id=source_dataset_id,
                source_version_id=source_version.id if source_version else None,
                target_dataset_id=target_dataset_id,
                source_field_key=source_field_key,
                target_field_key=target_field_key,
            )
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                if exc.constraint == "uq_datasets_key":
                    raise ConflictError(
                        "Dataset key already exists in this installation", field="key"
                    ) from exc
                raise
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset",
                object_id=dataset.id,
                action="created",
                details={"owner_team_id": owner_team_id, "creator_user_id": actor_id},
            )
            _version_reads(self).create_concept(actor_id, dataset.id, uow=uow)
            if kind == "mapping":
                if mapping_mode == "linked":
                    identifier_field = uow.dataset_fields.create(
                        dataset_id=dataset.id,
                        key=source_field.key,
                        name=source_field.name,
                        definition=source_field.definition,
                        unit=source_field.unit,
                        data_type=source_field.data_type,
                        is_required=True,
                        is_unique=True,
                        display_position=1,
                        mapping_role="source_identifier",
                        source_field_id=source_field.id,
                        source_field_key=source_field.key,
                    )
                    uow.dataset_business_keys.replace(dataset.id, (source_field.key,))
                    uow.flush()
                    for source_record in uow.versions.iterate_records(source_version):
                        source_value = next(
                            (
                                value
                                for value in source_record.values
                                if value.field_key == source_field.key
                            ),
                            None,
                        )
                        if source_value is None:
                            continue
                        encoded = encode_record_identifier(
                            {source_field.key: source_value.canonical_key},
                            expected_keys=(source_field.key,),
                        )
                        record = uow.dataset_records.create(
                            dataset_id=dataset.id, identifier_key=encoded
                        )
                        uow.flush()
                        uow.dataset_records.add_values(
                            record,
                            (
                                RecordValue(
                                    identifier_field.id,
                                    identifier_field.key,
                                    identifier_field.data_type,
                                    source_value.value,
                                    source_value.canonical_key,
                                    True,
                                ),
                            ),
                        )
                uow.flush()
            return dataset

    @staticmethod
    def _mapping_metadata(kind, mapping_mode, source_system, target_system):
        if kind not in {"reference", "mapping", "hierarchy"}:
            raise ValidationError("Choose a valid dataset type.")
        if kind in {"reference", "hierarchy"}:
            return kind, None, None, None
        if mapping_mode not in {None, "linked"}:
            raise ValidationError(
                "Mapping datasets require a source dataset with a Current version."
            )
        mapping_mode = "linked"

        def label(value, name):
            value = value.strip() if isinstance(value, str) else ""
            if len(value) > 200:
                raise ValidationError(f"{name} must be 200 characters or fewer.")
            return value or None

        return (
            kind,
            mapping_mode,
            label(source_system, "Source identifier"),
            label(target_system, "Target identifier"),
        )

    def change_owner(self, actor_id, key, expected_owner_team_id, owner_team_id):
        key = self._validated_lookup_key(key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(key, for_update=True)
            self._change_owner(uow, actor_id, dataset, expected_owner_team_id, owner_team_id)
            return dataset

    def _change_owner(self, uow, actor_id, dataset, expected_owner_team_id, owner_team_id):
        target_team = (
            uow.teams.get(owner_team_id, for_update=True) if valid_identity(owner_team_id) else None
        )
        # Validate authority before revealing owner or target information.
        if dataset is None or not self.authorization.datasets.can_change_dataset_owner(
            actor_id, dataset.id, uow=uow
        ):
            raise NotFoundError("Dataset not found")
        if not valid_identity(expected_owner_team_id):
            raise ValidationError("Expected owning team is required. Reopen the form.")
        if dataset.owner_team_id != expected_owner_team_id:
            raise StaleDatasetOwnerError("Owning team changed. Reopen the form and try again.")
        if target_team is None:
            raise ValidationError("Choose an existing owning team.")
        if owner_team_id == expected_owner_team_id:
            return dataset
        if not uow.datasets.change_owner_if_expected(
            dataset.id, expected_owner_team_id, owner_team_id
        ):
            raise StaleDatasetOwnerError("Owning team changed. Reopen the form and try again.")
        record_audit(
            uow,
            actor_user_id=actor_id,
            object_type="dataset",
            object_id=dataset.id,
            action="owner_changed",
            details={
                "previous_owner_team_id": expected_owner_team_id,
                "owner_team_id": owner_team_id,
            },
        )
        uow.flush()

    def export_access_context(self, actor_id, key):
        key = self._validated_lookup_key(key)
        with self.uow() as uow:
            uow.begin_export_snapshot()
            dataset = uow.datasets.find_by_key(key)
            if dataset is None or not self.authorization.datasets.can_maintain_dataset(
                actor_id, dataset.id, uow=uow
            ):
                raise NotFoundError("Dataset not found")
            policy = uow.installation_settings.get_policy()
            may_manage = self.authorization.datasets.can_manage_export_access(
                actor_id, dataset.id, uow=uow
            )
            if policy == "everyone":
                return dataset, [], [], [], policy, may_manage
            teams = uow.teams.list_by_name()
            users = uow.users.list_by_email()
            names = {("team", team.id): team.name for team in teams}
            names.update({("user", user.id): user.display_name for user in users})
            entries = [
                (entry, names.get((entry.subject_type, entry.subject_id), "Unavailable"))
                for entry in uow.dataset_export_access.list_for_dataset(dataset.id)
            ]
            return (
                dataset,
                teams,
                [user for user in users if user.is_active and not user.is_deleted],
                entries,
                policy,
                may_manage,
            )

    def grant_export_access(self, actor_id, key, subject_type, subject_id):
        key = self._validated_lookup_key(key)
        if not valid_identity(actor_id):
            raise NotFoundError("Dataset not found")
        with self.uow(write=True, serialized=True) as uow:
            # Serialize grant changes with policy changes before any Dataset/user locks.
            uow.installation_settings.get_policy(for_update=True)
            dataset = uow.datasets.find_by_key(key, for_update=True)
            valid = False
            if subject_type == "team" and valid_identity(subject_id):
                valid = uow.teams.get(subject_id, for_update=True) is not None
            elif subject_type == "user" and valid_identity(subject_id):
                locked_users = {
                    user_id: uow.users.get(user_id, for_update=True)
                    for user_id in sorted({subject_id, actor_id})
                }
                valid = self.authorization.datasets.active(locked_users[subject_id])
            if dataset is None or not self.authorization.datasets.can_manage_export_access(
                actor_id, dataset.id, uow=uow
            ):
                raise NotFoundError("Dataset not found")
            if subject_type not in SUBJECT_TYPES or not valid_identity(subject_id):
                raise ValidationError("Choose an available user or team.")
            if not valid:
                raise ValidationError("Choose an available user or team.")
            access = uow.dataset_export_access.create(dataset.id, subject_type, subject_id)
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                if exc.constraint in {
                    "uq_dataset_export_access_user",
                    "uq_dataset_export_access_team",
                }:
                    raise ConflictError("This user or team already has export access.") from exc
                raise
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_export_access",
                object_id=access.id,
                action="granted",
                details={
                    "dataset_id": dataset.id,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                },
            )
            return access

    def revoke_export_access(self, actor_id, key, access_id):
        key = self._validated_lookup_key(key)
        with self.uow(write=True, serialized=True) as uow:
            # Serialize grant changes with policy changes before any Dataset/user locks.
            uow.installation_settings.get_policy(for_update=True)
            dataset = uow.datasets.find_by_key(key, for_update=True)
            if dataset is None or not self.authorization.datasets.can_manage_export_access(
                actor_id, dataset.id, uow=uow
            ):
                raise NotFoundError("Dataset not found")
            access = uow.dataset_export_access.get(access_id) if valid_identity(access_id) else None
            if access is None or access.dataset_id != dataset.id:
                raise NotFoundError("Export access not found")
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_export_access",
                object_id=access.id,
                action="revoked",
                details={
                    "dataset_id": dataset.id,
                    "subject_type": access.subject_type,
                    "subject_id": access.subject_id,
                },
            )
            uow.dataset_export_access.delete(access)
            uow.flush()

    def list_datasets(self, actor_id: str) -> list[DatasetRecord]:
        return [row.dataset for row in self.list_dataset_overview(actor_id)]

    def list_dataset_overview(self, actor_id: str) -> list[DatasetOverview]:
        return _version_reads(self).overview(actor_id)

    def get_dataset_context(self, actor_id: str, key: str) -> DatasetOverview:
        return _version_reads(self).context(actor_id, key)

    def get_dataset(self, actor_id: str, key: str) -> DatasetRecord:
        return self.get_dataset_context(actor_id, key).dataset

    def get_visible_dataset_by_id(self, actor_id: str, dataset_id: str) -> DatasetRecord:
        with self.uow() as uow:
            dataset = uow.datasets.get(dataset_id)
            if dataset is None or not self.authorization.datasets.can_view_dataset(
                actor_id, dataset.id, uow=uow
            ):
                raise NotFoundError("Dataset not found")
            return dataset

    def get_dataset_details(self, actor_id: str, key: str) -> DatasetDetails:
        context = self.get_dataset_context(actor_id, key)
        dataset = context.dataset
        return DatasetDetails(
            id=dataset.id,
            key=dataset.key,
            name=dataset.name,
            description=dataset.description,
            owner_team_id=dataset.owner_team_id,
            owner_team_name=context.owner_team_name,
            record_count=context.record_count,
            last_updated=context.last_updated,
        )

    def get_dataset_configuration(self, actor_id: str, key: str) -> DatasetConfiguration:
        """Dataset metadata; unpublished existence remains maintainer-only."""
        key = self._validated_lookup_key(key)
        with self.uow() as uow:
            uow.begin_export_snapshot()
            dataset = uow.datasets.find_by_key(key)
            if dataset is None or not self.authorization.datasets.can_view_dataset(
                actor_id, dataset.id, uow=uow
            ):
                raise NotFoundError("Dataset not found")
            maintain = self.authorization.datasets.can_maintain_dataset(
                actor_id, dataset.id, uow=uow
            )
            if not maintain and uow.versions.latest_non_cancelled(dataset.id) is None:
                raise NotFoundError("Dataset not found")
            govern = self.authorization.datasets.can_change_dataset_owner(
                actor_id, dataset.id, uow=uow
            )
            may_edit_key = (
                maintain
                and uow.concepts.get(dataset.id) is not None
                and not uow.versions.list_for_dataset(dataset.id)
            )
            return DatasetConfiguration(dataset, maintain, govern, may_edit_key)

    def get_dataset_for_edit(self, actor_id: str, key: str) -> DatasetRecord:
        return self._get_dataset_with_access(actor_id, key, "can_maintain_dataset")

    def _get_dataset_with_access(
        self, actor_id: str, key: str, required_permission: str
    ) -> DatasetRecord:
        dataset = self._find_by_key(key)
        if not self.authorization.has_capability(actor_id, required_permission, dataset.id):
            raise NotFoundError("Dataset not found")
        return dataset

    def update_dataset(
        self,
        actor_id: str,
        key: str,
        name: str,
        description: str,
        *,
        owner_change: tuple[str, str] | None = None,
        icon: str | None = None,
        new_key: str | None = None,
    ) -> DatasetRecord:
        validated_key = self._validated_lookup_key(key)
        validated_new_key = validated_key if new_key is None else validate_dataset_key(new_key)
        normalized_name = normalize_dataset_name(name)
        validated_description = validate_dataset_description(description)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            if owner_change is not None:
                self._change_owner(uow, actor_id, dataset, *owner_change)
            if dataset is None or not self.authorization.has_capability(
                actor_id, "can_maintain_dataset", dataset.id, uow=uow
            ):
                raise NotFoundError("Dataset not found")
            validated_icon = dataset.icon if icon is None else validate_dataset_icon(icon)
            key_changed = dataset.key != validated_new_key
            if key_changed and (
                uow.concepts.get(dataset.id) is None or uow.versions.list_for_dataset(dataset.id)
            ):
                raise ValidationError(
                    "Dataset key can only be changed before the first version is published.",
                    field="key",
                )
            metadata_changed = (
                dataset.name != normalized_name or dataset.description != validated_description
            )
            if not key_changed and not metadata_changed and dataset.icon == validated_icon:
                return dataset
            if key_changed:
                try:
                    uow.datasets.rename_key(dataset.id, validated_new_key)
                except PersistenceConflictError as exc:
                    if exc.constraint == "uq_datasets_key":
                        raise ConflictError(
                            "Dataset key already exists in this installation", field="key"
                        ) from exc
                    raise
            dataset.icon = validated_icon
            dataset.name = normalized_name
            dataset.description = validated_description
            dataset.updated_at = uow.now()
            self._add_audit(
                uow,
                self._audit(actor_id, "dataset", dataset.id, "metadata_updated"),
            )
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                if exc.constraint == "uq_datasets_key":
                    raise ConflictError(
                        "Dataset key already exists in this installation", field="key"
                    ) from exc
                raise
            return dataset

    def _find_by_key(self, key: str) -> DatasetRecord:
        validated_key = self._validated_lookup_key(key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(validated_key)
            if dataset is None:
                raise NotFoundError("Dataset not found")
            return dataset

    @staticmethod
    def _validated_lookup_key(key: str) -> str:
        try:
            return validate_dataset_key(key)
        except ValidationError as exc:
            raise NotFoundError("Dataset not found") from exc

    @staticmethod
    def _audit(actor_id: str, object_type: str, object_id: str, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type=object_type,
            object_id=object_id,
            action=action,
        )

    _add_audit = staticmethod(UserService._add_audit)


class DatasetFieldService:
    """Authorized schema operations scoped through a parent Dataset."""

    def __init__(
        self, uow: UnitOfWorkFactory, authorization: AuthorizationService, *, versions=None
    ) -> None:
        self.uow = uow
        self.authorization = authorization
        self.versions = versions

    def create_field(
        self,
        actor_id: str,
        dataset_key: str,
        key: str,
        name: str,
        data_type: str,
        is_required: bool = False,
        is_unique: bool = False,
        is_business_key_component: bool = False,
        *,
        definition: str | None = None,
        unit: str | None = None,
        mapping_role: str | None = None,
        source_field_key: str | None = None,
        hierarchy_scope: str | None = None,
    ) -> DatasetFieldRecord:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            from refrncr.hierarchy import validate_hierarchy_scope

            hierarchy_scope = validate_hierarchy_scope(hierarchy_scope, dataset.kind)
            source_field = None
            source_dataset = None
            if dataset.kind == "mapping":
                mapping_role = mapping_role or "mapping_target"
                allowed_roles = (
                    {"mapping_target", "source_context"}
                    if dataset.mapping_mode == "linked"
                    else {"mapping_target", "source_identifier", "source_context"}
                )
                if mapping_role not in allowed_roles:
                    raise ValidationError("Choose a valid mapping field role")
                if mapping_role == "source_context":
                    if dataset.mapping_mode == "linked":
                        if not dataset.source_dataset_id:
                            raise ValidationError(
                                "Source context requires a linked mapping dataset"
                            )
                        source_dataset = uow.datasets.get(dataset.source_dataset_id)
                        if not source_field_key:
                            raise ValidationError("Choose an available source field")
                        source_version = uow.versions.get(
                            dataset.source_dataset_id, dataset.source_version_id
                        )
                        source_field = next(
                            (
                                field
                                for field in uow.versions.fields(source_version)
                                if field.key == self._validated_field_lookup_key(source_field_key)
                            ),
                            None,
                        )
                        if source_dataset is None or source_field is None:
                            raise ValidationError("Choose an available source field")
                        if any(
                            field.source_field_id == source_field.id
                            for field in uow.dataset_fields.list_for_dataset(dataset.id)
                        ):
                            raise ConflictError("Source field is already included in this mapping")
                        key, name, data_type = (
                            source_field.key,
                            source_field.name,
                            source_field.data_type,
                        )
                        definition, unit = source_field.definition, source_field.unit
                    else:
                        source_field_key = None
                    is_business_key_component = False
                else:
                    source_field_key = None
                    if mapping_role == "source_identifier":
                        existing_fields = uow.dataset_fields.list_for_dataset(dataset.id)
                        if any(
                            field.mapping_role == "source_identifier" for field in existing_fields
                        ):
                            raise ConflictError(
                                "A standalone mapping can have only one source identifier"
                            )
                        is_required = is_unique = is_business_key_component = True
                    else:
                        is_business_key_component = False
            else:
                mapping_role = source_field_key = None
            validated_required = validate_dataset_field_flag(is_required, "is_required")
            validated_component = validate_dataset_field_flag(
                is_business_key_component, "is_business_key_component"
            )
            if validated_component:
                _require_identifier_type(data_type)
            if validated_component and not validated_required:
                raise ValidationError("A Record identifier field must be required.")
            if uow.dataset_records.count(dataset.id) and (
                validated_required or validated_component
            ):
                raise ValidationError("Required fields cannot be added after records exist")
            definition, unit = validate_field_metadata(definition, unit)
            field = uow.dataset_fields.create(
                dataset_id=dataset.id,
                key=validate_dataset_field_key(key),
                name=normalize_dataset_field_name(name),
                definition=definition,
                unit=unit,
                data_type=validate_dataset_field_type(data_type),
                is_required=validated_required,
                is_unique=validate_dataset_field_flag(is_unique, "is_unique"),
                display_position=len(uow.dataset_fields.list_for_dataset(dataset.id)) + 1,
                mapping_role=mapping_role,
                source_field_id=source_field.id if source_field else None,
                source_field_key=source_field.key if source_field else None,
                hierarchy_scope=hierarchy_scope,
            )
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                if exc.constraint == "uq_dataset_fields_dataset_key":
                    raise ConflictError("Field key already exists in this Dataset") from exc
                raise
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            self._add_audit(uow, actor_id, field.id, "created")
            if validated_component:
                current_key = uow.dataset_business_keys.get(dataset.id)
                replacement_keys = tuple(sorted((*current_key.field_keys, field.key)))
                uow.dataset_business_keys.replace(dataset.id, replacement_keys)
                record_audit(
                    uow,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action=(
                        "business_key_defined"
                        if not current_key.field_keys
                        else "business_key_updated"
                    ),
                )
            if source_field is not None:
                ordered = uow.dataset_fields.list_for_dataset(dataset.id)
                uow.dataset_fields.replace_order(
                    dataset.id,
                    tuple(
                        item.id
                        for item in sorted(
                            ordered, key=lambda item: item.mapping_role == "mapping_target"
                        )
                    ),
                )
                mapping_records = {
                    identifier: record_id
                    for record_id, identifier in uow.dataset_records.identifier_keys(
                        dataset.id
                    ).items()
                }
                for source_record in uow.versions.iterate_records(source_version):
                    identifier_value = next(
                        (
                            value
                            for value in source_record.values
                            if value.field_key == dataset.source_field_key
                        ),
                        None,
                    )
                    context_value = next(
                        (
                            value
                            for value in source_record.values
                            if value.field_key == source_field.key
                        ),
                        None,
                    )
                    if identifier_value is None or context_value is None:
                        continue
                    encoded = encode_record_identifier(
                        {dataset.source_field_key: identifier_value.canonical_key},
                        expected_keys=(dataset.source_field_key,),
                    )
                    record_id = mapping_records.get(encoded)
                    if record_id:
                        uow.dataset_records.add_values_by_id(
                            dataset.id,
                            record_id,
                            (
                                RecordValue(
                                    field.id,
                                    field.key,
                                    field.data_type,
                                    context_value.value,
                                    context_value.canonical_key,
                                    False,
                                ),
                            ),
                        )
                uow.concepts.record_mutation(dataset.id)
            return field

    def list_fields(self, actor_id: str, dataset_key: str) -> list[DatasetFieldRecord]:
        return list(
            _version_reads(self).read(actor_id, dataset_key, page=None, prefer_concept=True).fields
        )

    def list_fields_for_datasets(
        self, actor_id: str, dataset_ids: tuple[str, ...]
    ) -> dict[str, tuple[DatasetFieldRecord, ...]]:
        """Read visible Dataset schemas in one snapshot and bounded query set."""
        dataset_ids = tuple(dict.fromkeys(dataset_ids))
        if not dataset_ids:
            return {}
        versions = _version_reads(self)
        with self.uow() as uow:
            uow.begin_export_snapshot()
            datasets = {dataset.id: dataset for dataset in uow.datasets.list_by_ids(dataset_ids)}
            if not self.authorization.datasets.can_create_dataset(actor_id, uow=uow):
                raise AuthorizationError("Active user access required")
            maintenance = self.authorization.datasets.maintainable_resources(actor_id, uow=uow)
            allowed = set(maintenance.resource_ids)
            concepts = uow.concepts.list_for_datasets(tuple(datasets))
            current = uow.versions.current_for_datasets(tuple(datasets), versions.clock.today())
            working_ids = tuple(
                dataset_id
                for dataset_id in dataset_ids
                if maintenance.all_resources or dataset_id in allowed
                if dataset_id in concepts or dataset_id not in current
            )
            working_fields = uow.dataset_fields.list_for_datasets(working_ids)
            version_fields = uow.versions.fields_for_versions(tuple(current.values()))
            result = {}
            for dataset_id in dataset_ids:
                if dataset_id not in datasets:
                    continue
                if (maintenance.all_resources or dataset_id in allowed) and (
                    dataset_id in concepts or dataset_id not in current
                ):
                    result[dataset_id] = tuple(working_fields.get(dataset_id, ()))
                elif dataset_id in current:
                    result[dataset_id] = tuple(version_fields.get(current[dataset_id].id, ()))
            return result

    def get_field(self, actor_id: str, dataset_key: str, field_key: str) -> DatasetFieldRecord:
        key = self._validated_field_lookup_key(field_key)
        field = next(
            (field for field in self.list_fields(actor_id, dataset_key) if field.key == key), None
        )
        if field is None:
            raise NotFoundError("Dataset field not found")
        return field

    def update_field(
        self,
        actor_id: str,
        dataset_key: str,
        field_key: str,
        name: str,
        data_type: str,
        is_required: bool,
        is_unique: bool,
        *,
        definition: str | None = None,
        unit: str | None = None,
        hierarchy_scope: str | None = None,
    ) -> DatasetFieldRecord:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            validated_field_key = self._validated_field_lookup_key(field_key)
            field = uow.dataset_fields.find_by_key(dataset.id, validated_field_key, for_update=True)
            if field is None:
                raise NotFoundError("Dataset field not found")
            if field.source_field_key:
                raise ValidationError("Source mapping fields are read-only")

            from refrncr.hierarchy import validate_hierarchy_scope

            hierarchy_scope = validate_hierarchy_scope(
                field.hierarchy_scope if hierarchy_scope is None else hierarchy_scope, dataset.kind
            )

            definition, unit = validate_field_metadata(
                field.definition if definition is None else definition,
                field.unit if unit is None else unit,
            )
            normalized_name = normalize_dataset_field_name(name)
            validated_type = validate_dataset_field_type(data_type)
            validated_required = validate_dataset_field_flag(is_required, "is_required")
            validated_unique = validate_dataset_field_flag(is_unique, "is_unique")
            if (
                field.is_required
                and not validated_required
                and uow.dataset_business_keys.contains(dataset.id, field.key)
            ):
                raise ValidationError("A Record identifier field must be required.")
            replacement = (
                normalized_name,
                validated_type,
                validated_required,
                validated_unique,
                definition,
                unit,
                hierarchy_scope,
            )
            if uow.dataset_business_keys.contains(dataset.id, field.key):
                _require_identifier_type(validated_type)
            current = (
                field.name,
                field.data_type,
                field.is_required,
                field.is_unique,
                field.definition,
                field.unit,
                field.hierarchy_scope,
            )
            if replacement == current:
                return field

            if dataset.kind == "hierarchy":
                self._validate_hierarchy_definition_change(uow, dataset, field, validated_type)
            else:
                self._validate_record_aware_definition_change(
                    uow, dataset.id, field, validated_type, validated_required, validated_unique
                )
            if dataset.kind != "hierarchy" and validated_unique != field.is_unique:
                uow.dataset_records.set_field_unique_keys(dataset.id, field.id, validated_unique)

            field.definition, field.unit = definition, unit
            field.name = normalized_name
            field.data_type = validated_type
            field.is_required = validated_required
            field.is_unique = validated_unique
            field.hierarchy_scope = hierarchy_scope
            field.updated_at = uow.now()
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            self._add_audit(uow, actor_id, field.id, "definition_updated")
            return field

    def update_field_and_business_key_membership(
        self,
        actor_id: str,
        dataset_key: str,
        field_key: str,
        name: str,
        data_type: str,
        is_required: bool,
        is_unique: bool,
        is_business_key_component: bool,
        *,
        new_key: str | None = None,
        definition: str | None = None,
        unit: str | None = None,
        hierarchy_scope: str | None = None,
    ) -> DatasetFieldRecord:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            validated_field_key = self._validated_field_lookup_key(field_key)
            field = uow.dataset_fields.find_by_key(dataset.id, validated_field_key, for_update=True)
            if field is None:
                raise NotFoundError("Dataset field not found")
            validated_new_key = (
                field.key if new_key is None else validate_dataset_field_key(new_key)
            )
            key_changed = validated_new_key != field.key
            if key_changed and field.source_field_key:
                raise ValidationError("Source mapping field keys are read-only")
            if key_changed and (
                uow.concepts.get(dataset.id) is None or uow.versions.list_for_dataset(dataset.id)
            ):
                raise ValidationError(
                    "Field key can only be changed before the first version is published."
                )
            if key_changed and uow.datasets.uses_linked_field(dataset.id, field.key):
                raise ValidationError(
                    "Field key cannot change while used by a linked mapping dataset."
                )
            existing_key = uow.dataset_fields.find_by_key(dataset.id, validated_new_key)
            if key_changed and existing_key is not None:
                raise ConflictError("Field key already exists in this Dataset")

            from refrncr.hierarchy import validate_hierarchy_scope

            hierarchy_scope = validate_hierarchy_scope(
                field.hierarchy_scope if hierarchy_scope is None else hierarchy_scope, dataset.kind
            )

            definition, unit = validate_field_metadata(
                field.definition if definition is None else definition,
                field.unit if unit is None else unit,
            )
            normalized_name = normalize_dataset_field_name(name)
            validated_type = validate_dataset_field_type(data_type)
            validated_required = validate_dataset_field_flag(is_required, "is_required")
            validated_unique = validate_dataset_field_flag(is_unique, "is_unique")
            validated_component = validate_dataset_field_flag(
                is_business_key_component, "is_business_key_component"
            )
            if dataset.kind == "mapping":
                if field.mapping_role == "source_identifier":
                    validated_required = validated_unique = validated_component = True
                else:
                    validated_component = False
            if validated_component:
                _require_identifier_type(data_type)
            if validated_component and not validated_required:
                raise ValidationError("A Record identifier field must be required.")

            current_key = uow.dataset_business_keys.get(dataset.id)
            currently_component = field.key in current_key.field_keys
            membership_changed = currently_component != validated_component
            replacement = (
                normalized_name,
                validated_type,
                validated_required,
                validated_unique,
                definition,
                unit,
                hierarchy_scope,
            )
            current_definition = (
                field.name,
                field.data_type,
                field.is_required,
                field.is_unique,
                field.definition,
                field.unit,
                field.hierarchy_scope,
            )
            definition_changed = replacement != current_definition
            if not membership_changed and not definition_changed and not key_changed:
                return field

            if membership_changed and uow.dataset_records.count(dataset.id):
                raise ValidationError("Record identifier cannot change after records exist")
            if definition_changed:
                if dataset.kind == "hierarchy":
                    self._validate_hierarchy_definition_change(uow, dataset, field, validated_type)
                else:
                    self._validate_record_aware_definition_change(
                        uow, dataset.id, field, validated_type, validated_required, validated_unique
                    )
                if dataset.kind != "hierarchy" and validated_unique != field.is_unique:
                    uow.dataset_records.set_field_unique_keys(
                        dataset.id, field.id, validated_unique
                    )

            identifier_changed = membership_changed or (key_changed and currently_component)
            replacement_keys = set(current_key.field_keys)
            if key_changed:
                if currently_component:
                    replacement_keys.discard(field.key)
                    uow.dataset_business_keys.replace(dataset.id, tuple(sorted(replacement_keys)))
                try:
                    field = uow.dataset_fields.rename_key(dataset.id, field.id, validated_new_key)
                except PersistenceConflictError as exc:
                    if exc.constraint == "uq_dataset_fields_dataset_key":
                        raise ConflictError("Field key already exists in this Dataset") from exc
                    raise
                uow.dataset_records.rename_deleted_field_key(
                    dataset.id, field.id, validated_new_key
                )

            if identifier_changed:
                replacement_keys = set(current_key.field_keys)
                replacement_keys.discard(validated_field_key)
                if validated_component:
                    replacement_keys.add(validated_new_key)
                uow.dataset_business_keys.replace(dataset.id, tuple(sorted(replacement_keys)))
                record_audit(
                    uow,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset.id,
                    action=(
                        "business_key_defined"
                        if not current_key.field_keys
                        else "business_key_updated"
                    ),
                )

            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            if definition_changed or key_changed:
                field.definition, field.unit = definition, unit
                field.name = normalized_name
                field.data_type = validated_type
                field.is_required = validated_required
                field.is_unique = validated_unique
                field.hierarchy_scope = hierarchy_scope
                field.updated_at = uow.now()
                self._add_audit(uow, actor_id, field.id, "definition_updated")
            return field

    def delete_field(self, actor_id: str, dataset_key: str, field_key: str) -> None:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            validated_field_key = self._validated_field_lookup_key(field_key)
            field = uow.dataset_fields.find_by_key(dataset.id, validated_field_key, for_update=True)
            if field is None:
                raise NotFoundError("Dataset field not found")
            if uow.dataset_business_keys.contains(dataset.id, field.key):
                raise ValidationError(
                    "Remove this field from the Record identifier before deleting it"
                )
            if uow.datasets.uses_linked_field(dataset.id, field.key):
                raise ValidationError("A field used by a linked mapping dataset cannot be deleted")
            if uow.dataset_records.field_has_values(dataset.id, field.id) or (
                dataset.kind == "hierarchy" and uow.hierarchy.field_has_values(dataset.id, field.id)
            ):
                raise ValidationError("A field with stored values cannot be deleted")
            field_id = field.id
            uow.dataset_fields.delete(field)
            uow.flush()
            remaining = uow.dataset_fields.list_for_dataset(dataset.id)
            uow.dataset_fields.replace_order(dataset.id, tuple(item.id for item in remaining))
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            self._add_audit(uow, actor_id, field_id, "deleted")

    def replace_order(
        self,
        actor_id: str,
        dataset_key: str,
        expected_order: tuple[str, ...],
        proposed_order: tuple[str, ...],
    ) -> tuple[DatasetFieldRecord, ...]:
        validated_dataset_key = self._validated_dataset_lookup_key(dataset_key)
        if not isinstance(expected_order, tuple) or not isinstance(proposed_order, tuple):
            raise ValidationError("Field order must be a complete sequence")
        if any(
            not isinstance(value, str) or not value for value in (*expected_order, *proposed_order)
        ):
            raise ValidationError("Field order contains a malformed field identity")
        if len(set(expected_order)) != len(expected_order) or len(set(proposed_order)) != len(
            proposed_order
        ):
            raise ValidationError("Field order contains duplicate fields")
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            current = tuple(field.id for field in fields)
            if expected_order != current:
                raise StaleFieldOrderError("Field order changed since this page was opened")
            if len(proposed_order) != len(current) or set(proposed_order) != set(current):
                raise ValidationError("Field order must contain every current field exactly once")
            if dataset.kind == "mapping":
                by_id = {field.id: field for field in fields}
                roles = [
                    by_id[field_id].mapping_role == "mapping_target" for field_id in proposed_order
                ]
                if roles != sorted(roles):
                    raise ValidationError("Move fields within their Source or Target block.")
            if proposed_order == current:
                return tuple(fields)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            uow.dataset_fields.replace_order(dataset.id, proposed_order)
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset",
                object_id=dataset.id,
                action="field_order_updated",
            )
            by_id = {field.id: field for field in fields}
            return tuple(by_id[field_id] for field_id in proposed_order)

    def move_field(
        self,
        actor_id: str,
        dataset_key: str,
        field_id: str,
        direction: str,
        expected_order: tuple[str, ...],
    ) -> tuple[DatasetFieldRecord, ...]:
        if direction not in {"up", "down"}:
            raise ValidationError("Field direction is invalid")
        if not isinstance(field_id, str) or not field_id:
            raise NotFoundError("Dataset field not found")
        if field_id not in expected_order:
            raise NotFoundError("Dataset field not found")
        proposed = list(expected_order)
        index = proposed.index(field_id)
        target = index - 1 if direction == "up" else index + 1
        if target < 0 or target >= len(proposed):
            return self.replace_order(actor_id, dataset_key, expected_order, expected_order)
        proposed[index], proposed[target] = proposed[target], proposed[index]
        return self.replace_order(actor_id, dataset_key, expected_order, tuple(proposed))

    def _require_dataset_access(
        self, actor_id: str, dataset: DatasetRecord | None, required_permission: str, *, uow=None
    ) -> None:
        if dataset is None or not self.authorization.has_capability(
            actor_id, required_permission, dataset.id, uow=uow
        ):
            raise NotFoundError("Dataset not found")
        if required_permission == "can_maintain_dataset":
            if uow is not None:
                require_concept(uow, dataset.id)
            else:
                with self.uow() as work:
                    require_concept(work, dataset.id)

    @staticmethod
    def _validated_dataset_lookup_key(key: str) -> str:
        try:
            return validate_dataset_key(key)
        except ValidationError as exc:
            raise NotFoundError("Dataset not found") from exc

    @staticmethod
    def _validated_field_lookup_key(key: str) -> str:
        try:
            return validate_dataset_field_key(key)
        except ValidationError as exc:
            raise NotFoundError("Dataset field not found") from exc

    @staticmethod
    def _add_audit(uow: UnitOfWork, actor_id: str, field_id: str, action: str) -> None:
        record_audit(
            uow,
            actor_user_id=actor_id,
            object_type="dataset_field",
            object_id=field_id,
            action=action,
        )

    @staticmethod
    def _validate_hierarchy_definition_change(uow, dataset, field, data_type):
        # Inactive values still belong to this field. Changing their datatype would
        # destroy their lossless recovery contract. Applicability changes never delete.
        if data_type != field.data_type and uow.hierarchy.field_has_values(dataset.id, field.id):
            raise ValidationError("Field type cannot change while stored values exist")

    @staticmethod
    def _validate_record_aware_definition_change(
        uow: UnitOfWork,
        dataset_id: str,
        field: DatasetFieldRecord,
        data_type: str,
        is_required: bool,
        is_unique: bool,
    ) -> None:
        if data_type != field.data_type and uow.dataset_records.field_has_values(
            dataset_id, field.id
        ):
            raise ValidationError("Field type cannot change while stored values exist")
        if (
            not field.is_required
            and is_required
            and uow.dataset_records.field_has_missing_values(dataset_id, field.id)
        ):
            raise ValidationError("Required cannot be enabled while records have no value")
        if (
            not field.is_unique
            and is_unique
            and uow.dataset_records.field_has_duplicate_values(dataset_id, field.id)
        ):
            raise ValidationError("Unique cannot be enabled while duplicate values exist")


class DatasetBusinessKeyService:
    """Authorized business-key metadata operations scoped through a parent Dataset."""

    def __init__(
        self, uow: UnitOfWorkFactory, authorization: AuthorizationService, *, versions=None
    ) -> None:
        self.uow = uow
        self.authorization = authorization
        self.versions = versions

    def get_business_key(self, actor_id: str, dataset_key: str) -> DatasetBusinessKey:
        return (
            _version_reads(self)
            .read(actor_id, dataset_key, page=None, prefer_concept=True)
            .identifier
        )

    def set_business_key(
        self, actor_id: str, dataset_key: str, field_keys: tuple[str, ...]
    ) -> DatasetBusinessKey:
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            if not isinstance(field_keys, tuple) or not field_keys:
                raise ValidationError("Select at least one Record identifier field.")
            if any(not isinstance(field_key, str) for field_key in field_keys):
                raise ValidationError("Record identifier field keys must be text.")
            if len(set(field_keys)) != len(field_keys):
                raise ValidationError("Record identifier fields must not contain duplicates.")

            components: list[str] = []
            for submitted_key in field_keys:
                try:
                    field_key = validate_dataset_field_key(submitted_key)
                except ValidationError as exc:
                    raise ValidationError("Selected Dataset field does not exist") from exc
                field = uow.dataset_fields.find_by_key(dataset.id, field_key)
                if field is None:
                    raise ValidationError("Selected Dataset field does not exist")
                _require_identifier_type(field.data_type)
                if not field.is_required:
                    raise ValidationError("A Record identifier field must be required.")
                components.append(field.key)

            canonical = tuple(sorted(components))
            if dataset.kind == "mapping" and canonical != (dataset.source_field_key,):
                raise ValidationError("The mapping source identifier cannot change.")
            current = uow.dataset_business_keys.get(dataset.id)
            if current.field_keys == canonical:
                return current

            if uow.dataset_records.count(dataset.id):
                raise ValidationError("Record identifier cannot change after records exist")

            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            uow.dataset_business_keys.replace(dataset.id, canonical)
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset",
                object_id=dataset.id,
                action=(
                    "business_key_defined" if not current.field_keys else "business_key_updated"
                ),
            )
            return DatasetBusinessKey(dataset_id=dataset.id, field_keys=canonical)

    def _require_dataset_access(
        self, actor_id: str, dataset: DatasetRecord | None, required_permission: str, *, uow=None
    ) -> None:
        if dataset is None or not self.authorization.has_capability(
            actor_id, required_permission, dataset.id, uow=uow
        ):
            raise NotFoundError("Dataset not found")
        if required_permission == "can_maintain_dataset":
            if uow is not None:
                require_concept(uow, dataset.id)
            else:
                with self.uow() as work:
                    require_concept(work, dataset.id)


class DatasetRecordService:
    """Authorized current-record operations scoped through a parent Dataset."""

    PAGE_SIZE = 50

    def __init__(
        self, uow: UnitOfWorkFactory, authorization: AuthorizationService, *, versions=None
    ) -> None:
        self.uow = uow
        self.authorization = authorization
        self.versions = versions

    def create_record(
        self, actor_id: str, dataset_key: str, submitted: dict[str, str]
    ) -> StoredRecord:
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            if dataset.kind == "mapping" and dataset.mapping_mode == "linked":
                raise ValidationError(
                    "Linked mapping records are populated from the source dataset"
                )
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            if dataset.kind == "mapping":
                if message := mapping_incomplete_message(fields):
                    raise ValidationError(message)
            identifier = uow.dataset_business_keys.get(dataset.id)
            values, encoded_identifier = self._validated_values(fields, identifier, submitted)
            unique_values = tuple(
                (value.field_id, value.canonical_key) for value in values if value.is_unique
            )
            existing_unique = uow.dataset_records.existing_unique_value_keys(
                dataset.id, unique_values
            )
            if existing_unique:
                field_by_id = {field.id: field for field in fields}
                conflicting_field = field_by_id[next(iter(existing_unique))[0]]
                raise RecordConflictError(
                    f"{conflicting_field.name} must be unique; this value already exists",
                    conflicting_field.key,
                )
            record = uow.dataset_records.create(
                dataset_id=dataset.id, identifier_key=encoded_identifier
            )
            try:
                uow.flush()
                uow.dataset_records.add_values(record, tuple(values))
                uow.flush()
            except PersistenceConflictError as error:
                if error.constraint == "uq_dataset_records_dataset_identifier":
                    raise RecordConflictError("Record identifier already exists") from error
                if error.constraint == "uq_dataset_record_values_unique_value":
                    raise RecordConflictError("A unique field value already exists") from error
                raise
            uow.concepts.record_mutation(dataset.id)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record.id,
                action="created",
            )
            return StoredRecord(record.id, dataset.id, tuple(values), 1)

    def get_record_for_edit(self, actor_id: str, dataset_key: str, record_id: str) -> StoredRecord:
        dataset = self._find_dataset(actor_id, dataset_key, "can_maintain_dataset")
        with self.uow() as uow:
            record = uow.dataset_records.get(dataset.id, record_id)
        if record is None:
            raise NotFoundError("Record not found")
        return record

    def update_record(
        self,
        actor_id: str,
        dataset_key: str,
        record_id: str,
        expected_revision: int,
        submitted: dict[str, str],
    ) -> StoredRecord:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValidationError("Expected revision must be a positive integer")
        if not 1 <= expected_revision <= MAX_RECORD_REVISION:
            raise ValidationError("Expected revision is out of range")
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            existing = uow.dataset_records.get(dataset.id, record_id)
            if existing is None:
                raise NotFoundError("Record not found")
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            for field in fields:
                if not field.source_field_key:
                    continue
                existing_value = next(
                    (value for value in existing.values if value.field_id == field.id), None
                )
                submitted.pop(field.key, None)
                if existing_value is not None:
                    submitted[field.key] = get_datatype(field.data_type).text(existing_value.value)
            identifier = uow.dataset_business_keys.get(dataset.id)
            values, encoded_identifier = self._validated_values(fields, identifier, submitted)
            # Check stale input before accepting a normalized no-op. The Dataset
            # lock serializes supported writes throughout this comparison.
            if existing.revision != expected_revision:
                raise StaleRecordError("Record changed since this form was opened")
            if {value.field_id: value.canonical_key for value in existing.values} == {
                value.field_id: value.canonical_key for value in values
            }:
                return existing
            try:
                updated = uow.dataset_records.update_if_revision(
                    dataset.id, record_id, expected_revision, encoded_identifier
                )
                if not updated:
                    self._raise_missing_or_stale(uow, dataset.id, record_id)
                uow.dataset_records.replace_values(existing, values)
                uow.flush()
            except PersistenceConflictError as error:
                self._raise_record_conflict(error)
            uow.concepts.record_mutation(dataset.id)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record_id,
                action="updated",
            )
            return StoredRecord(record_id, dataset.id, values, expected_revision + 1)

    def delete_record(
        self, actor_id: str, dataset_key: str, record_id: str, expected_revision: int
    ) -> None:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValidationError("Expected revision must be a positive integer")
        if not 1 <= expected_revision < MAX_RECORD_REVISION:
            raise ValidationError("Expected revision is out of range")
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_key, for_update=True)
            self._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            if dataset.kind == "mapping":
                raise ValidationError(
                    "Source records are read-only; edit the target values instead."
                )
            if not uow.dataset_records.delete_if_revision(dataset.id, record_id, expected_revision):
                self._raise_missing_or_stale(uow, dataset.id, record_id)
            uow.concepts.record_mutation(dataset.id)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record_id,
                action="deleted",
            )

    def restore_record(self, actor_id, dataset_key, record_id, expected_revision, *, concept_id):
        if type(expected_revision) is not int or not 1 <= expected_revision < MAX_RECORD_REVISION:
            raise ValidationError("Expected revision is out of range")
        key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(key, for_update=True)
            self._require_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            if dataset.kind == "mapping":
                raise ValidationError(
                    "Source records can only be restored by updating the source version."
                )
            if require_concept(uow, dataset.id).id != concept_id:
                raise NotFoundError("Record not found")
            deleted = next(
                (
                    r
                    for r, _fields in uow.dataset_records.deleted_records(dataset.id)
                    if r.id == record_id
                ),
                None,
            )
            if deleted is None:
                raise NotFoundError("Record not found")
            if deleted.revision != expected_revision:
                raise StaleRecordError("Record changed since this form was opened")
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            by_id = {f.id: f for f in fields}
            if any(
                v.field_id not in by_id or by_id[v.field_id].data_type != v.data_type
                for v in deleted.values
            ):
                raise ValidationError(
                    "The Field schema changed. This Record cannot be restored without changing its "
                    "retained values. Review its values before publishing or discarding "
                    "this Concept."
                )
            submitted = {
                v.field_key: get_datatype(v.data_type).text(v.value) for v in deleted.values
            }
            values, encoded = self._validated_values(
                fields, uow.dataset_business_keys.get(dataset.id), submitted
            )
            try:
                if not uow.dataset_records.restore_if_revision(
                    dataset.id, record_id, expected_revision, encoded
                ):
                    raise StaleRecordError("Record changed since this form was opened")
                uow.dataset_records.add_values(deleted, values)
                uow.flush()
            except PersistenceConflictError as error:
                self._raise_record_conflict(error)
            uow.concepts.record_mutation(dataset.id)
            advance_concept(uow, dataset)
            dataset.content_updated_at = uow.now()
            record_audit(
                uow,
                actor_user_id=actor_id,
                object_type="dataset_record",
                object_id=record_id,
                action="restored",
            )
            return StoredRecord(record_id, dataset.id, values, expected_revision + 1)

    @staticmethod
    def _raise_missing_or_stale(uow: UnitOfWork, dataset_id: str, record_id: str) -> None:
        if uow.dataset_records.current_revision(dataset_id, record_id) is None:
            raise NotFoundError("Record not found")
        raise StaleRecordError("Record changed since this form was opened")

    @staticmethod
    def _raise_record_conflict(error: PersistenceConflictError) -> None:
        if error.constraint == "uq_dataset_records_dataset_identifier":
            raise RecordConflictError("Record identifier already exists") from error
        if error.constraint == "uq_dataset_record_values_unique_value":
            raise RecordConflictError("A unique field value already exists") from error
        raise error

    @staticmethod
    def _validated_values(fields, identifier, submitted):
        field_by_key = {field.key: field for field in fields}
        unknown = sorted(set(submitted) - set(field_by_key))
        if unknown:
            raise RecordValidationError({key: "Unknown Dataset field" for key in unknown})
        if not identifier.field_keys:
            raise ValidationError("Configure a Record identifier before adding records")
        for key in identifier.field_keys:
            if key not in field_by_key:
                raise ValidationError("Invalid Record identifier definition")
            _require_identifier_type(field_by_key[key].data_type)
        errors: dict[str, str] = {}
        values: list[RecordValue] = []
        for field in fields:
            raw = submitted.get(field.key, "")
            try:
                canonical = canonicalize_record_value(field.data_type, raw)
            except ValidationError as error:
                errors[field.key] = str(error)
                continue
            if canonical is None:
                if field.is_required or field.key in identifier.field_keys:
                    errors[field.key] = "A value is required"
                continue
            value, key = canonical
            values.append(
                RecordValue(field.id, field.key, field.data_type, value, key, field.is_unique)
            )
        if errors:
            raise RecordValidationError(errors)
        value_by_key = {value.field_key: value for value in values}
        encoded_identifier = encode_record_identifier(
            {key: value_by_key[key].canonical_key for key in identifier.field_keys},
            expected_keys=identifier.field_keys,
        )
        return tuple(values), encoded_identifier

    def list_records(self, actor_id: str, dataset_key: str, page: int = 1) -> RecordPage:
        return _version_reads(self).read(actor_id, dataset_key, page=page, prefer_concept=True).page

    def export_dataset(
        self,
        actor_id: str,
        dataset_key: str,
        format_name: str,
        *,
        version_id=None,
        prefer_concept=False,
        variant=None,
    ):
        from refrncr.dataset_export import (
            ExportGenerationError,
            externally_ordered_records,
            generate_export,
            verify_artifact_readable,
        )

        if not isinstance(format_name, str) or format_name not in {
            "csv",
            "csv_zip",
            "xlsx",
            "json",
        }:
            raise ExportGenerationError("Choose exactly one supported export format")
        if variant is not None and variant not in {"data", "flattened", "reporting"}:
            raise ExportGenerationError(
                "Choose Data, Flattened or Reporting for a hierarchy export"
            )
        validated_key = DatasetFieldService._validated_dataset_lookup_key(dataset_key)
        artifact = None
        try:
            with self.uow(translate_errors=True) as uow:
                uow.begin_export_snapshot()
                dataset = uow.datasets.find_by_key(validated_key)
                self._require_access(actor_id, dataset, "can_export_dataset", uow=uow)
                dataset_id = dataset.id
                _dataset, selected, _maintain = _version_reads(self)._selection(
                    uow, actor_id, validated_key, version_id, prefer_concept=prefer_concept
                )
                concept_identity = None
                if prefer_concept:
                    concept = uow.concepts.get(dataset_id)
                    if selected is not None or concept is None:
                        raise NotFoundError("Dataset not found")
                    concept_identity = concept.id
                if selected is None:
                    fields = tuple(uow.dataset_fields.list_for_dataset(dataset_id))
                    identifier = uow.dataset_business_keys.get(dataset_id)
                    source = uow.dataset_records.iterate_all(dataset_id)
                    export_key = (
                        f"{dataset.key}-concept"
                        if dataset.kind == "hierarchy" and variant == "reporting"
                        else dataset.key
                    )
                    hierarchy_nodes = (
                        tuple(uow.hierarchy.list_for_dataset(dataset_id))
                        if dataset.kind == "hierarchy"
                        else None
                    )
                    provenance = {"type": "concept"} if prefer_concept else {"type": "current"}
                else:
                    if selected.status not in {"Current", "Expired"}:
                        raise NotFoundError("Dataset not found")
                    fields = uow.versions.fields(selected.version)
                    identifier = DatasetBusinessKey(
                        dataset_id,
                        tuple(sorted(field.key for field in fields if field.is_identifier)),
                    )
                    source = uow.versions.iterate_records(selected.version)
                    export_key = f"{dataset.key}-v{selected.version.number}"
                    hierarchy_nodes = (
                        tuple(uow.versions.hierarchy_nodes(selected.version))
                        if dataset.kind == "hierarchy"
                        else None
                    )
                    provenance = {
                        "type": "publication",
                        "version": selected.version.number,
                        "version_id": selected.version.id,
                    }
                records = externally_ordered_records(
                    source,
                    lambda record: self._record_sort_key(record, identifier.field_keys),
                )
                if (
                    dataset.kind == "hierarchy"
                    and variant in {"data", "flattened"}
                    and format_name != "csv"
                ):
                    raise ExportGenerationError("A hierarchy variant can only be selected for CSV")
                if (
                    dataset.kind == "hierarchy"
                    and variant == "reporting"
                    and format_name not in {"csv", "xlsx"}
                ):
                    raise ExportGenerationError("Reporting tables are available as CSV or Excel")
                if dataset.kind != "hierarchy" and variant is not None:
                    raise ExportGenerationError(
                        "CSV variants are only available for hierarchy exports"
                    )
                if format_name == "csv_zip" and dataset.kind != "hierarchy":
                    raise ExportGenerationError(
                        "CSV bundles are only available for hierarchy exports"
                    )
                if hierarchy_nodes is None and variant is None and format_name != "csv_zip":
                    artifact = generate_export(format_name, fields, records, export_key)
                else:
                    artifact = generate_export(
                        format_name,
                        fields,
                        records,
                        export_key,
                        hierarchy_nodes=hierarchy_nodes,
                        hierarchy_variant=variant or "data",
                        provenance=provenance,
                    )
                verify_artifact_readable(artifact)
            # Authorization and content were pinned to the completed snapshot.
            # Close it before acquiring the audit writer (including SQLite).
            with self.uow(
                write=True, serialized=concept_identity is not None, translate_errors=True
            ) as uow:
                if concept_identity is not None:
                    _version_reads(self)._target(uow, actor_id, dataset_id, write=True)
                    concept = uow.concepts.get(dataset_id)
                    if concept is None or concept.id != concept_identity:
                        raise NotFoundError("Dataset not found")
                record_audit(
                    uow,
                    actor_user_id=actor_id,
                    object_type="dataset",
                    object_id=dataset_id,
                    action="dataset.export_requested",
                    details={"format": format_name},
                )
            return artifact
        except BaseException:
            if artifact is not None:
                artifact.content.close()
            raise

    @staticmethod
    def _record_sort_key(record: StoredRecord, identifier_keys: tuple[str, ...]):
        by_key = {value.field_key: value for value in record.values}
        incomplete = any(key not in by_key for key in identifier_keys)
        result = []
        for key in identifier_keys:
            value = by_key.get(key)
            if value is None:
                result.append((1, None))
                continue
            result.append(get_datatype(value.data_type).ordering_key(value.value))
        return incomplete, tuple(result), record.id

    def get_dataset_for_add(self, actor_id: str, dataset_key: str) -> DatasetRecord:
        return self._find_dataset(actor_id, dataset_key, "can_maintain_dataset")

    def _find_dataset(self, actor_id: str, key: str, permission: str) -> DatasetRecord:
        validated = DatasetFieldService._validated_dataset_lookup_key(key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(validated)
        self._require_access(actor_id, dataset, permission)
        return dataset

    def _require_access(
        self, actor_id: str, dataset: DatasetRecord | None, permission: str, *, uow=None
    ) -> None:
        if dataset is None or not self.authorization.has_capability(
            actor_id, permission, dataset.id, uow=uow
        ):
            raise NotFoundError("Dataset not found")
        if dataset.kind == "hierarchy" and permission != "can_export_dataset":
            raise ValidationError(
                "Use the hierarchy views to maintain this table. "
                "Hierarchy import is not available yet."
            )
        if permission == "can_maintain_dataset":
            if uow is not None:
                require_concept(uow, dataset.id)
            else:
                with self.uow() as work:
                    require_concept(work, dataset.id)
