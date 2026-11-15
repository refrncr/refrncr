from dataclasses import dataclass

from refrncr.audit import record_audit
from refrncr.domain import valid_identity
from refrncr.errors import DatasetConflictError, DatasetNotFoundError, DatasetValidationError


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


def version_reads(service):
    from refrncr.dataset_versions import DatasetVersionService

    return service.versions or DatasetVersionService(service.uow, service.authorization.datasets)


def mapping_deletion_error(count):
    if count:
        dependency = "1 mapping is" if count == 1 else f"{count} mappings are"
        return f"This dataset can't be deleted because {dependency} based on it."
    return None


class DatasetDeletionMixin:
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
                raise DatasetConflictError(
                    "Dataset changed. Reopen the confirmation and try again."
                )
            if not isinstance(confirmation_name, str) or confirmation_name != dataset.name:
                raise DatasetValidationError(
                    "Enter the current dataset name exactly to confirm deletion."
                )
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
            raise DatasetNotFoundError("Dataset not found")
        dataset = uow.datasets.get(dataset_id, for_update=for_update)
        if dataset is None or not self.authorization.datasets.can_maintain_dataset(
            actor_id, dataset.id, uow=uow
        ):
            raise DatasetNotFoundError("Dataset not found")
        if error := mapping_deletion_error(uow.datasets.mapping_usage_count(dataset.id)):
            raise DatasetConflictError(error)
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
