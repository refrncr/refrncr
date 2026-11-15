from refrncr.datasets.datatypes import get_datatype

from .deletion import mapping_deletion_error, version_reads
from .errors import DatasetNotFoundError


class DatasetMappingMixin:
    def mapping_usage(self, actor_id, key, *, details=True):
        """Dataset-wide dependants, with private version details removed before presentation."""
        from refrncr.version_contracts import version_states

        with self.uow() as uow:
            uow.begin_export_snapshot()
            source = uow.datasets.find_by_key(self._validated_lookup_key(key))
            if source is None or not self.authorization.has_capability(
                actor_id, "can_view_dataset", source.id, uow=uow
            ):
                raise DatasetNotFoundError("Dataset not found")
            result = {"key": source.key, "total": 0, "mappings": [], "restricted": []}
            if not details:
                result["total"] = uow.datasets.mapping_usage_count(source.id)
                return result
            mappings = uow.datasets.mappings_using(source.id)
            result["total"] = len(mappings)
            result["deletion_error"] = mapping_deletion_error(len(mappings))
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
            today = version_reads(self).clock.today()
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
                raise DatasetNotFoundError("Dataset not found")
            if dataset.kind != "mapping":
                return None
            maintain = self.authorization.has_capability(
                actor_id, "can_maintain_dataset", dataset.id, uow=uow
            )
            source_id = dataset.source_version_id
            if not version_id and not maintain:
                raise DatasetNotFoundError("Dataset version not found")
            if version_id:
                version = uow.versions.get(dataset.id, version_id)
                if (
                    version is None
                    or version.cancelled_at is not None
                    or (version.effective_date > version_reads(self).clock.today() and not maintain)
                ):
                    raise DatasetNotFoundError("Dataset version not found")
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
                            or published.effective_date > version_reads(self).clock.today()
                        )
                    ):
                        raise DatasetNotFoundError("Dataset version not found")
                    pinned = source_versions.get(published.source_version_id)
                    if pinned is None:
                        raise DatasetNotFoundError("Source version not found")
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
            from refrncr.datasets.mapping_updates import source_update_blocker

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
                from refrncr.datasets.mapping_updates import mapping_states

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
                (source.id,), version_reads(self).clock.today()
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
        contexts = version_reads(self).overview(actor_id)
        with self.uow() as uow:
            uow.begin_export_snapshot()
            ids = tuple(
                context.dataset.id
                for context in contexts
                if context.dataset.kind == "reference" and not context.no_current
            )
            versions = uow.versions.current_for_datasets(ids, version_reads(self).clock.today())
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
