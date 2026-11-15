from __future__ import annotations

from refrncr.audit import record_audit
from refrncr.authorization import AuthorizationService
from refrncr.datasets.deletion import version_reads as _version_reads
from refrncr.datasets.errors import (
    DatasetAuthorizationError,
    DatasetConflictError,
    DatasetNotFoundError,
    DatasetValidationError,
    StaleFieldOrderError,
)
from refrncr.datasets.record_identity import encode_record_identifier
from refrncr.datasets.shared import (
    normalize_dataset_field_name,
    validate_dataset_field_flag,
    validate_dataset_field_key,
    validate_dataset_field_type,
    validate_dataset_key,
    validate_field_metadata,
)
from refrncr.datasets.shared import (
    require_identifier_type as _require_identifier_type,
)
from refrncr.domain import advance_concept, require_concept
from refrncr.errors import ValidationError
from refrncr.ports import (
    DatasetFieldRecord,
    DatasetRecord,
    PersistenceConflictError,
    RecordValue,
    UnitOfWork,
    UnitOfWorkFactory,
)


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
                    raise DatasetValidationError("Choose a valid mapping field role")
                if mapping_role == "source_context":
                    if dataset.mapping_mode == "linked":
                        if not dataset.source_dataset_id:
                            raise DatasetValidationError(
                                "Source context requires a linked mapping dataset"
                            )
                        source_dataset = uow.datasets.get(dataset.source_dataset_id)
                        if not source_field_key:
                            raise DatasetValidationError("Choose an available source field")
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
                            raise DatasetValidationError("Choose an available source field")
                        if any(
                            field.source_field_id == source_field.id
                            for field in uow.dataset_fields.list_for_dataset(dataset.id)
                        ):
                            raise DatasetConflictError(
                                "Source field is already included in this mapping"
                            )
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
                            raise DatasetConflictError(
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
                raise DatasetValidationError("A Record identifier field must be required.")
            if uow.dataset_records.count(dataset.id) and (
                validated_required or validated_component
            ):
                raise DatasetValidationError("Required fields cannot be added after records exist")
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
                    raise DatasetConflictError("Field key already exists in this Dataset") from exc
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
                self._populate_source_context(uow, dataset, field, source_field, source_version)
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
                raise DatasetAuthorizationError("Active user access required")
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
            raise DatasetNotFoundError("Dataset field not found")
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
                raise DatasetNotFoundError("Dataset field not found")
            if field.source_field_key:
                raise DatasetValidationError("Source mapping fields are read-only")

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
                raise DatasetValidationError("A Record identifier field must be required.")
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
                raise DatasetNotFoundError("Dataset field not found")
            validated_new_key = (
                field.key if new_key is None else validate_dataset_field_key(new_key)
            )
            key_changed = self._validate_field_key_change(uow, dataset, field, validated_new_key)

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
                raise DatasetValidationError("A Record identifier field must be required.")

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
                raise DatasetValidationError("Record identifier cannot change after records exist")
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
                        raise DatasetConflictError(
                            "Field key already exists in this Dataset"
                        ) from exc
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
                raise DatasetNotFoundError("Dataset field not found")
            if uow.dataset_business_keys.contains(dataset.id, field.key):
                raise DatasetValidationError(
                    "Remove this field from the Record identifier before deleting it"
                )
            if uow.datasets.uses_linked_field(dataset.id, field.key):
                raise DatasetValidationError(
                    "A field used by a linked mapping dataset cannot be deleted"
                )
            if uow.dataset_records.field_has_values(dataset.id, field.id) or (
                dataset.kind == "hierarchy" and uow.hierarchy.field_has_values(dataset.id, field.id)
            ):
                raise DatasetValidationError("A field with stored values cannot be deleted")
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
            raise DatasetValidationError("Field order must be a complete sequence")
        if any(
            not isinstance(value, str) or not value for value in (*expected_order, *proposed_order)
        ):
            raise DatasetValidationError("Field order contains a malformed field identity")
        if len(set(expected_order)) != len(expected_order) or len(set(proposed_order)) != len(
            proposed_order
        ):
            raise DatasetValidationError("Field order contains duplicate fields")
        with self.uow(write=True, serialized=True) as uow:
            dataset = uow.datasets.find_by_key(validated_dataset_key, for_update=True)
            self._require_dataset_access(actor_id, dataset, "can_maintain_dataset", uow=uow)
            fields = uow.dataset_fields.list_for_dataset(dataset.id)
            current = tuple(field.id for field in fields)
            if expected_order != current:
                raise StaleFieldOrderError("Field order changed since this page was opened")
            if len(proposed_order) != len(current) or set(proposed_order) != set(current):
                raise DatasetValidationError(
                    "Field order must contain every current field exactly once"
                )
            if dataset.kind == "mapping":
                by_id = {field.id: field for field in fields}
                roles = [
                    by_id[field_id].mapping_role == "mapping_target" for field_id in proposed_order
                ]
                if roles != sorted(roles):
                    raise DatasetValidationError("Move fields within their Source or Target block.")
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
            raise DatasetValidationError("Field direction is invalid")
        if not isinstance(field_id, str) or not field_id:
            raise DatasetNotFoundError("Dataset field not found")
        if field_id not in expected_order:
            raise DatasetNotFoundError("Dataset field not found")
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
            raise DatasetNotFoundError("Dataset not found")
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
            raise DatasetNotFoundError("Dataset not found") from exc

    @staticmethod
    def _validated_field_lookup_key(key: str) -> str:
        try:
            return validate_dataset_field_key(key)
        except ValidationError as exc:
            raise DatasetNotFoundError("Dataset field not found") from exc

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
            raise DatasetValidationError("Field type cannot change while stored values exist")

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
            raise DatasetValidationError("Field type cannot change while stored values exist")
        if (
            not field.is_required
            and is_required
            and uow.dataset_records.field_has_missing_values(dataset_id, field.id)
        ):
            raise DatasetValidationError("Required cannot be enabled while records have no value")
        if (
            not field.is_unique
            and is_unique
            and uow.dataset_records.field_has_duplicate_values(dataset_id, field.id)
        ):
            raise DatasetValidationError("Unique cannot be enabled while duplicate values exist")

    @staticmethod
    def _populate_source_context(uow, dataset, field, source_field, source_version) -> None:
        ordered = uow.dataset_fields.list_for_dataset(dataset.id)
        uow.dataset_fields.replace_order(
            dataset.id,
            tuple(
                item.id
                for item in sorted(ordered, key=lambda item: item.mapping_role == "mapping_target")
            ),
        )
        mapping_records = {
            identifier: record_id
            for record_id, identifier in uow.dataset_records.identifier_keys(dataset.id).items()
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
                (value for value in source_record.values if value.field_key == source_field.key),
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

    @staticmethod
    def _validate_field_key_change(uow, dataset, field, validated_new_key: str) -> bool:
        key_changed = validated_new_key != field.key
        if key_changed and field.source_field_key:
            raise DatasetValidationError("Source mapping field keys are read-only")
        if key_changed and (
            uow.concepts.get(dataset.id) is None or uow.versions.list_for_dataset(dataset.id)
        ):
            raise DatasetValidationError(
                "Field key can only be changed before the first version is published."
            )
        if key_changed and uow.datasets.uses_linked_field(dataset.id, field.key):
            raise DatasetValidationError(
                "Field key cannot change while used by a linked mapping dataset."
            )
        existing_key = uow.dataset_fields.find_by_key(dataset.id, validated_new_key)
        if key_changed and existing_key is not None:
            raise DatasetConflictError("Field key already exists in this Dataset")
        return key_changed
