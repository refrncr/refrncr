from __future__ import annotations

import re
from dataclasses import dataclass

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
from refrncr.datasets import DatasetConflictError as DatasetConflictError
from refrncr.datasets import DatasetDeletion as DatasetDeletion
from refrncr.datasets import DatasetDetails as DatasetDetails
from refrncr.datasets import DatasetError as DatasetError
from refrncr.datasets import DatasetNotFoundError as DatasetNotFoundError
from refrncr.datasets import DatasetService as DatasetService
from refrncr.datasets import DatasetValidationError as DatasetValidationError
from refrncr.datasets import RecordConflictError as RecordConflictError
from refrncr.datasets import RecordValidationError as RecordValidationError
from refrncr.datasets import StaleDatasetOwnerError as StaleDatasetOwnerError
from refrncr.datasets import StaleFieldOrderError as StaleFieldOrderError
from refrncr.datasets import StaleRecordError as StaleRecordError
from refrncr.datasets import canonicalize_record_value as canonicalize_record_value
from refrncr.datasets import normalize_dataset_field_name as normalize_dataset_field_name
from refrncr.datasets import normalize_dataset_name as normalize_dataset_name
from refrncr.datasets import require_identifier_type as _require_identifier_type
from refrncr.datasets import validate_dataset_description as validate_dataset_description
from refrncr.datasets import validate_dataset_field_flag as validate_dataset_field_flag
from refrncr.datasets import validate_dataset_field_key as validate_dataset_field_key
from refrncr.datasets import validate_dataset_field_type as validate_dataset_field_type
from refrncr.datasets import validate_dataset_icon as validate_dataset_icon
from refrncr.datasets import validate_dataset_key as validate_dataset_key
from refrncr.datasets import validate_field_metadata as validate_field_metadata
from refrncr.datasets import version_reads as _version_reads
from refrncr.datatypes import DATASET_FIELD_TYPES as DATASET_FIELD_TYPES
from refrncr.datatypes import get_datatype
from refrncr.domain import advance_concept, require_concept
from refrncr.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
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
    DatasetBusinessKey,
    DatasetFieldRecord,
    DatasetRecord,
    PersistenceConflictError,
    RecordValue,
    StoredRecord,
    UnitOfWork,
    UnitOfWorkFactory,
)
from refrncr.record_identity import encode_record_identifier
from refrncr.version_contracts import mapping_incomplete_message

SUBJECT_TYPES = {"user", "team"}

# Compatibility facade while services move to domain packages. These assignments keep
# existing imports working without making ``errors.py`` depend on the facade.
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
class RecordPage:
    records: tuple[StoredRecord, ...]
    page: int
    page_size: int
    total: int


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
