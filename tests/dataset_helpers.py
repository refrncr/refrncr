"""Explicit Dataset ownership setup for pre-REF-73 behavior regression tests."""


def create_owned_dataset(service, actor_id, key, name, description, **kwargs):
    # The owning team is fixture data, never an automatic application fallback.
    with service.uow(write=True, serialized=True) as work:
        work.users.lock_community_setup()
        teams = work.teams.list_by_name()
        owner = next((team for team in teams if team.name == "Test data domain"), None)
        if owner is None:
            owner = work.teams.create(name="Test data domain")
            work.flush()
        owner_id = owner.id
    return service.create_dataset(
        actor_id, key, name, description, owner_team_id=owner_id, **kwargs
    )


def downgrade_historical_fixture(config, revision):
    """Construct a pre-REF-73 fixture, then exercise the requested older downgrade.

    REF-73 intentionally refuses populated downgrades. Older migration regression
    tests need historical rows, not a bypass in that production migration. This
    test-only fixture removes the new columns before stamping the old baseline;
    REF-73 upgrade/downgrade/rollback is tested separately with unmodified Alembic.
    """
    from alembic import command
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    from refrncr.db.session import create_database_engine

    supplied = config.attributes.get("connection")
    engine = (
        None
        if supplied is not None
        else create_database_engine(config.get_main_option("sqlalchemy.url"))
    )
    connection = supplied if supplied is not None else engine.connect()
    try:
        current = connection.scalar(text("SELECT version_num FROM alembic_version"))
        connection.commit()
        if current == "0023_ref_176":
            command.downgrade(config, "0022_ref_31")
            current = "0022_ref_31"
        if current == "0022_ref_31":
            command.downgrade(config, "0021_ref_40")
            current = "0021_ref_40"
        if current == "0021_ref_40":
            command.downgrade(config, "0020_ref_162")
            current = "0020_ref_162"
        if current == "0020_ref_162":
            command.downgrade(config, "0019_ref_162")
            current = "0019_ref_162"
        if current == "0019_ref_162":
            command.downgrade(config, "0018_ref_160")
            current = "0018_ref_160"
        if current == "0018_ref_160":
            command.downgrade(config, "0017_ref_154")
            current = "0017_ref_154"
        if current == "0017_ref_154":
            command.downgrade(config, "0016_ref_84")
            current = "0016_ref_84"
        if current == "0016_ref_84":
            # Historical fixture construction only; lifecycle downgrade safety has
            # dedicated tests. Preserve every working-content and audit row.
            with connection.begin():
                connection.execute(text("DELETE FROM dataset_concepts"))
            command.downgrade(config, "0015_ref_83")
            current = "0015_ref_83"
        if current == "0015_ref_83":
            # This helper constructs historical fixtures, not a production downgrade.
            # REF-83's populated downgrade guard is tested independently.
            with connection.begin():
                connection.execute(text("DELETE FROM dataset_versions"))
            command.downgrade(config, "0014_ref_145")
            current = "0014_ref_145"
        if current == "0014_ref_145":
            # REF-145 retains old IDs and permits a lossless downgrade of old types.
            command.downgrade(config, "0013_ref_39")
            current = "0013_ref_39"
        if current == "0013_ref_39":
            with connection.begin():
                Operations(MigrationContext.configure(connection)).drop_index(
                    "ix_audit_events_timestamp_id", table_name="audit_events"
                )
                connection.execute(text("UPDATE alembic_version SET version_num='0012_ref_73'"))
            current = "0012_ref_73"
        if current == "0012_ref_73":
            mapping = dict(
                connection.execute(text("SELECT key, owner_team_id FROM datasets")).all()
            )
            config.attributes["dataset_owner_mapping"] = mapping
            assert connection.scalar(text("SELECT count(*) FROM dataset_export_access")) == 0
            connection.commit()
            sqlite = connection.dialect.name == "sqlite"
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
            with connection.begin():
                if sqlite:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                operations = Operations(MigrationContext.configure(connection))
                operations.drop_table("dataset_export_access")
                with operations.batch_alter_table("datasets") as batch:
                    batch.drop_index("ix_datasets_owner_team_id")
                    for foreign_key in inspect(connection).get_foreign_keys("datasets"):
                        if (
                            foreign_key["constrained_columns"] == ["owner_team_id"]
                            and foreign_key["name"]
                        ):
                            batch.drop_constraint(foreign_key["name"], type_="foreignkey")
                    batch.drop_column("owner_team_id")
                with operations.batch_alter_table("audit_events") as batch:
                    batch.drop_column("details")
                connection.execute(text("UPDATE alembic_version SET version_num='0011_ref_130'"))
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                connection.commit()
    finally:
        if supplied is None:
            connection.close()
            engine.dispose()
    command.downgrade(config, revision)


def publish_dataset(services, actor_id, dataset):
    """Explicitly prepare and publish a readable fixture under REF-83's rules."""
    from refrncr.datatypes import get_datatype
    from refrncr.models import utc_now

    fields = services.dataset_fields.list_fields(actor_id, dataset.key)
    identifier = services.dataset_business_keys.get_business_key(actor_id, dataset.key)
    if not identifier.field_keys:
        eligible = next(
            (
                field
                for field in fields
                if field.is_required and get_datatype(field.data_type).identifier_eligible
            ),
            None,
        )
        if eligible:
            services.dataset_business_keys.set_business_key(actor_id, dataset.key, (eligible.key,))
        else:
            services.dataset_fields.create_field(
                actor_id,
                dataset.key,
                "publication_id",
                "Publication identifier",
                "string",
                is_required=True,
                is_business_key_component=True,
            )
    target = services.datasets.get_dataset(actor_id, dataset.key)
    return services.dataset_versions.publish(
        actor_id, dataset.id, utc_now().date(), "", target.revision
    )
