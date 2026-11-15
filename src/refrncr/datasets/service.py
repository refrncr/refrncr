from refrncr.audit import record_audit
from refrncr.authorization import AuthorizationService
from refrncr.datatypes import get_datatype
from refrncr.domain import valid_identity
from refrncr.errors import (
    AuthorizationError,
)
from refrncr.identity import UserService
from refrncr.ports import (
    AuditEntry,
    DatasetConfiguration,
    DatasetOverview,
    DatasetRecord,
    PersistenceConflictError,
    RecordValue,
    UnitOfWorkFactory,
)
from refrncr.record_identity import encode_record_identifier

from .deletion import DatasetDeletionMixin, version_reads
from .errors import (
    DatasetConflictError,
    DatasetNotFoundError,
    DatasetValidationError,
    StaleDatasetOwnerError,
)
from .mapping import DatasetMappingMixin
from .shared import (
    DatasetDetails,
    normalize_dataset_name,
    validate_dataset_description,
    validate_dataset_icon,
    validate_dataset_key,
)

SUBJECT_TYPES = {"user", "team"}


class DatasetService(DatasetDeletionMixin, DatasetMappingMixin):
    """Authorized Dataset operations within the one Community installation context."""

    def __init__(
        self, uow: UnitOfWorkFactory, authorization: AuthorizationService, *, versions=None
    ) -> None:
        self.uow = uow
        self.authorization = authorization
        self.versions = versions

    def get_dataset_for_deletion(self, actor_id: str, dataset_id: str):
        return super().get_dataset_for_deletion(actor_id, dataset_id)

    def delete_dataset(
        self, actor_id: str, dataset_id: str, confirmation_name: str, expected_updated_at: str
    ):
        return super().delete_dataset(actor_id, dataset_id, confirmation_name, expected_updated_at)

    def mapping_usage(self, actor_id, key, *, details=True):
        return super().mapping_usage(actor_id, key, details=details)

    def mapping_source(self, actor_id, key, version_id=None, *, history_version_ids=()):
        return super().mapping_source(
            actor_id, key, version_id, history_version_ids=history_version_ids
        )

    def mapping_source_choices(self, actor_id):
        return super().mapping_source_choices(actor_id)

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
                    raise DatasetValidationError(
                        "Choose an owning team. Team membership is required."
                    )
                owner_team_id = eligible[0].id
            if not self.authorization.datasets.can_create_dataset(actor_id, owner_team_id, uow=uow):
                if not self.authorization.datasets.active(uow.users.get(actor_id)):
                    raise AuthorizationError("Active user access required")
                raise DatasetValidationError(
                    "Choose an available owning team you may create datasets for."
                )
            kind, mapping_mode, source_system, target_system = self._mapping_metadata(
                kind, mapping_mode, source_system, target_system
            )
            source_version = None
            if kind == "mapping" and mapping_mode == "linked":
                if not source_dataset_id:
                    raise DatasetValidationError("Choose a source dataset for a mapping dataset.")
                linked = uow.datasets.get(source_dataset_id)
                visible = linked is not None and self.authorization.has_capability(
                    actor_id, "can_view_dataset", linked.id, uow=uow
                )
                if not visible or linked.kind != "reference":
                    raise DatasetValidationError("Choose an available reference source dataset.")
                source_version = uow.versions.current_for_datasets(
                    (linked.id,), version_reads(self).clock.today()
                ).get(linked.id)
                if source_version is None:
                    raise DatasetValidationError(
                        f"Source dataset {linked.name} must have a Current version."
                    )
                source_fields = {field.key: field for field in uow.versions.fields(source_version)}
                if not source_field_key or source_field_key not in source_fields:
                    raise DatasetValidationError("Choose an available source identifier.")
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
                    raise DatasetValidationError("Choose a required, unique source identifier.")
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
                    raise DatasetConflictError(
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
            version_reads(self).create_concept(actor_id, dataset.id, uow=uow)
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
            raise DatasetValidationError("Choose a valid dataset type.")
        if kind in {"reference", "hierarchy"}:
            return kind, None, None, None
        if mapping_mode not in {None, "linked"}:
            raise DatasetValidationError(
                "Mapping datasets require a source dataset with a Current version."
            )
        mapping_mode = "linked"

        def label(value, name):
            value = value.strip() if isinstance(value, str) else ""
            if len(value) > 200:
                raise DatasetValidationError(f"{name} must be 200 characters or fewer.")
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
            raise DatasetNotFoundError("Dataset not found")
        if not valid_identity(expected_owner_team_id):
            raise DatasetValidationError("Expected owning team is required. Reopen the form.")
        if dataset.owner_team_id != expected_owner_team_id:
            raise StaleDatasetOwnerError("Owning team changed. Reopen the form and try again.")
        if target_team is None:
            raise DatasetValidationError("Choose an existing owning team.")
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
                raise DatasetNotFoundError("Dataset not found")
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
            raise DatasetNotFoundError("Dataset not found")
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
                raise DatasetNotFoundError("Dataset not found")
            if subject_type not in SUBJECT_TYPES or not valid_identity(subject_id):
                raise DatasetValidationError("Choose an available user or team.")
            if not valid:
                raise DatasetValidationError("Choose an available user or team.")
            access = uow.dataset_export_access.create(dataset.id, subject_type, subject_id)
            try:
                uow.flush()
            except PersistenceConflictError as exc:
                if exc.constraint in {
                    "uq_dataset_export_access_user",
                    "uq_dataset_export_access_team",
                }:
                    raise DatasetConflictError(
                        "This user or team already has export access."
                    ) from exc
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
                raise DatasetNotFoundError("Dataset not found")
            access = uow.dataset_export_access.get(access_id) if valid_identity(access_id) else None
            if access is None or access.dataset_id != dataset.id:
                raise DatasetNotFoundError("Export access not found")
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
        return version_reads(self).overview(actor_id)

    def get_dataset_context(self, actor_id: str, key: str) -> DatasetOverview:
        return version_reads(self).context(actor_id, key)

    def get_dataset(self, actor_id: str, key: str) -> DatasetRecord:
        return self.get_dataset_context(actor_id, key).dataset

    def get_visible_dataset_by_id(self, actor_id: str, dataset_id: str) -> DatasetRecord:
        with self.uow() as uow:
            dataset = uow.datasets.get(dataset_id)
            if dataset is None or not self.authorization.datasets.can_view_dataset(
                actor_id, dataset.id, uow=uow
            ):
                raise DatasetNotFoundError("Dataset not found")
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
                raise DatasetNotFoundError("Dataset not found")
            maintain = self.authorization.datasets.can_maintain_dataset(
                actor_id, dataset.id, uow=uow
            )
            if not maintain and uow.versions.latest_non_cancelled(dataset.id) is None:
                raise DatasetNotFoundError("Dataset not found")
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
            raise DatasetNotFoundError("Dataset not found")
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
                raise DatasetNotFoundError("Dataset not found")
            validated_icon = dataset.icon if icon is None else validate_dataset_icon(icon)
            key_changed = dataset.key != validated_new_key
            if key_changed and (
                uow.concepts.get(dataset.id) is None or uow.versions.list_for_dataset(dataset.id)
            ):
                raise DatasetValidationError(
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
                        raise DatasetConflictError(
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
                    raise DatasetConflictError(
                        "Dataset key already exists in this installation", field="key"
                    ) from exc
                raise
            return dataset

    def _find_by_key(self, key: str) -> DatasetRecord:
        validated_key = self._validated_lookup_key(key)
        with self.uow() as uow:
            dataset = uow.datasets.find_by_key(validated_key)
            if dataset is None:
                raise DatasetNotFoundError("Dataset not found")
            return dataset

    @staticmethod
    def _validated_lookup_key(key: str) -> str:
        try:
            return validate_dataset_key(key)
        except DatasetValidationError as exc:
            raise DatasetNotFoundError("Dataset not found") from exc

    @staticmethod
    def _audit(actor_id: str, object_type: str, object_id: str, action: str) -> AuditEntry:
        return AuditEntry(
            actor_user_id=actor_id,
            object_type=object_type,
            object_id=object_id,
            action=action,
        )

    _add_audit = staticmethod(UserService._add_audit)
