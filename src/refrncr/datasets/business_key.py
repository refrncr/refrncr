from __future__ import annotations

from typing import TYPE_CHECKING

from refrncr.audit import record_audit
from refrncr.datasets.deletion import version_reads as _version_reads
from refrncr.datasets.errors import DatasetNotFoundError, DatasetValidationError
from refrncr.datasets.fields import DatasetFieldService
from refrncr.datasets.shared import (
    require_identifier_type as _require_identifier_type,
)
from refrncr.datasets.shared import (
    validate_dataset_field_key,
)
from refrncr.domain import advance_concept, require_concept
from refrncr.errors import ValidationError
from refrncr.ports import (
    DatasetBusinessKey,
    DatasetRecord,
    UnitOfWorkFactory,
)

if TYPE_CHECKING:
    from refrncr.authorization import AuthorizationService


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
                raise DatasetValidationError("Select at least one Record identifier field.")
            if any(not isinstance(field_key, str) for field_key in field_keys):
                raise DatasetValidationError("Record identifier field keys must be text.")
            if len(set(field_keys)) != len(field_keys):
                raise DatasetValidationError(
                    "Record identifier fields must not contain duplicates."
                )

            components: list[str] = []
            for submitted_key in field_keys:
                try:
                    field_key = validate_dataset_field_key(submitted_key)
                except ValidationError as exc:
                    raise DatasetValidationError("Selected Dataset field does not exist") from exc
                field = uow.dataset_fields.find_by_key(dataset.id, field_key)
                if field is None:
                    raise DatasetValidationError("Selected Dataset field does not exist")
                _require_identifier_type(field.data_type)
                if not field.is_required:
                    raise DatasetValidationError("A Record identifier field must be required.")
                components.append(field.key)

            canonical = tuple(sorted(components))
            if dataset.kind == "mapping" and canonical != (dataset.source_field_key,):
                raise DatasetValidationError("The mapping source identifier cannot change.")
            current = uow.dataset_business_keys.get(dataset.id)
            if current.field_keys == canonical:
                return current

            if uow.dataset_records.count(dataset.id):
                raise DatasetValidationError("Record identifier cannot change after records exist")

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
            raise DatasetNotFoundError("Dataset not found")
        if required_permission == "can_maintain_dataset":
            if uow is not None:
                require_concept(uow, dataset.id)
            else:
                with self.uow() as work:
                    require_concept(work, dataset.id)
