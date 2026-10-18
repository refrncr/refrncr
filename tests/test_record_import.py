import io
import os
import re
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from dataset_helpers import create_owned_dataset
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

import refrncr.record_import as record_import_module
from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, DatasetRecord, DatasetRecordValue
from refrncr.record_import import (
    MAX_COMPRESSION_RATIO,
    DatasetRecordImportService,
    ImportValidationError,
    ParsedSource,
    SourceRow,
    TransientImportStore,
    build_mapping_assistance,
    normalize_mapping_name,
    parse_source,
    profile_source_columns,
    rank_mapping_candidates,
)
from refrncr.services import NotFoundError, ResourceTypeRegistry
from refrncr.web import create_flask_app

PASSWORD = "correct horse battery staple"


def field(key, name, data_type="string", *, required=False):
    return SimpleNamespace(
        key=key,
        name=name,
        data_type=data_type,
        is_required=required,
    )


@pytest.mark.parametrize(
    "header",
    [
        "country_code",
        " Country-Code ",
        "countryCode",
        "Country   code",
        "COUNTRY CODE",
        "countrý code",
        "country\N{COMBINING ACUTE ACCENT} code",
        "Country!!!Code",
    ],
)
def test_mapping_header_normalization_is_deterministic(header):
    assert normalize_mapping_name(header) == normalize_mapping_name(header)
    assert normalize_mapping_name(header) == "country code"


def test_duplicate_smart_normalized_headers_are_rejected():
    with pytest.raises(ImportValidationError, match="unique"):
        parse_source("duplicate.csv", b"country_code,Country-Code\nNL,BE\n")

    with pytest.raises(ImportValidationError, match="meaningful header"):
        parse_source("punctuation.csv", b"!!!,code\nvalue,A\n")


def test_acronym_and_non_latin_mapping_normalization():
    assert normalize_mapping_name("ISO2CountryCode") == "iso2 country code"
    assert normalize_mapping_name("国番号") == "国番号"


def test_type_profiling_uses_existing_grammars_and_is_bounded():
    rows = tuple(
        SourceRow(
            index + 2,
            (
                str(index),
                "1e2" if index == 1 else "1.5",
                "true" if index % 2 else "false",
                "2024-02-29" if index != 2 else "2024-02-30",
                "",
            ),
            frozenset({4}) if index == 0 else frozenset(),
        )
        for index in range(150)
    )
    source = ParsedSource("csv", ("text", "number", "boolean", "date", "formula"), rows, 0)
    profiles = profile_source_columns(source)
    assert len(profiles[0].values) == 25
    assert "number" in profiles[0].compatible_types
    assert "number" not in profiles[1].compatible_types
    assert "boolean" in profiles[2].compatible_types
    assert "date" not in profiles[3].compatible_types
    assert profiles[4].display_values == ()
    assert all(value != "149" for value in profiles[0].values)

    long_source = ParsedSource("csv", ("long",), (SourceRow(2, ("<b>" + "x" * 100,)),), 0)
    displayed = profile_source_columns(long_source)[0].display_values[0]
    assert len(displayed) == 80
    assert displayed.endswith("…")


def test_candidate_ranking_name_primary_type_support_and_tie_breaker():
    source = ParsedSource(
        "csv",
        ("country_code", "validDate", "country identifier", "unrelated"),
        (SourceRow(2, ("NL", "2024-01-01", "NL", "2024-01-01")),),
        0,
    )
    fields = (
        field("country_code", "ISO code"),
        field("other", "country_code"),
        field("valid_date", "Valid date", "date"),
        field("country_name", "Country name"),
        field("country_id", "Country identifier"),
    )
    profiles = profile_source_columns(source)
    exact = rank_mapping_candidates(source.headers[0], profiles[0], fields)
    assert exact[0].field_key == "country_code"
    assert exact[0].confidence == "High"
    assert rank_mapping_candidates(source.headers[1], profiles[1], fields)[0].field_key == (
        "valid_date"
    )
    token = rank_mapping_candidates(source.headers[2], profiles[2], fields)
    assert token[0].field_key == "country_id"
    assert len(token) <= 3
    assert rank_mapping_candidates(source.headers[3], profiles[3], fields) == ()
    tied = rank_mapping_candidates(
        "country",
        profiles[0],
        (field("beta", "Country"), field("alpha", "Country")),
    )
    assert [candidate.field_key for candidate in tied] == sorted(
        candidate.field_key for candidate in tied
    )
    exact_key_stays_first = rank_mapping_candidates(
        "valid_date",
        profiles[1],
        (
            field("valid_date", "Unrelated", "boolean"),
            field("other", "Valid date", "date"),
        ),
    )
    assert [candidate.field_key for candidate in exact_key_stays_first] == [
        "valid_date",
        "other",
    ]
    assert exact_key_stays_first[0].confidence == "Low"


def test_auto_selection_requires_unique_strong_match_and_resolves_duplicate_targets():
    fields = (
        field("country_code", "Country code", required=True),
        field("other_code", "Country code"),
    )
    identifier = SimpleNamespace(field_keys=("country_code",))
    strong = ParsedSource("csv", ("countryCode",), (SourceRow(2, ("NL",)),), 0)
    assert build_mapping_assistance(strong, fields, identifier).mapping == ("country_code",)
    ambiguous = ParsedSource("csv", ("Country code",), (SourceRow(2, ("NL",)),), 0)
    ambiguous_fields = (
        field("alpha", "Country code"),
        field("beta", "Country code"),
    )
    advice = build_mapping_assistance(ambiguous, ambiguous_fields, SimpleNamespace(field_keys=()))
    assert advice.mapping == ("",)
    assert advice.columns[0].warning == "Multiple possible fields — choose one"
    duplicate = ParsedSource("csv", ("country_code", "ISO code"), (SourceRow(2, ("NL", "BE")),), 0)
    duplicate_field = (field("country_code", "ISO code", required=True),)
    advice = build_mapping_assistance(duplicate, duplicate_field, identifier)
    assert advice.mapping == ("country_code", "")
    assert advice.columns[1].warning is not None


@pytest.mark.parametrize("header", ["country_code", "Country-Code", "countryCode", "Country code"])
def test_separator_variants_strongly_select_unique_display_name(header):
    source = ParsedSource("csv", (header,), (SourceRow(2, ("NL",)),), 0)
    fields = (field("cur_iso", "Country code", required=True),)
    advice = build_mapping_assistance(source, fields, SimpleNamespace(field_keys=("cur_iso",)))
    assert advice.mapping == ("cur_iso",)
    assert advice.columns[0].candidates[0].confidence == "High"


def test_medium_token_match_and_type_conflict_are_never_auto_selected():
    fields = (field("country_code", "Country code"),)
    medium = ParsedSource("csv", ("Country identifier",), (SourceRow(2, ("NL",)),), 0)
    medium_advice = build_mapping_assistance(medium, fields, SimpleNamespace(field_keys=()))
    assert medium_advice.columns[0].candidates[0].confidence == "Medium"
    assert medium_advice.mapping == ("",)

    conflicting = ParsedSource("csv", ("valid_date",), (SourceRow(2, ("not-a-date",)),), 0)
    conflict_advice = build_mapping_assistance(
        conflicting,
        (field("valid_date", "Valid date", "date"),),
        SimpleNamespace(field_keys=()),
    )
    assert conflict_advice.columns[0].candidates[0].confidence == "Low"
    assert "do not match" in conflict_advice.columns[0].candidates[0].reason
    assert conflict_advice.mapping == ("",)

    typed_choice = build_mapping_assistance(
        ParsedSource("csv", ("Value",), (SourceRow(2, ("2024-01-01",)),), 0),
        (field("text", "Value"), field("day", "Value", "date")),
        SimpleNamespace(field_keys=()),
    )
    assert typed_choice.columns[0].candidates[0].field_key == "day"
    assert typed_choice.mapping == ("day",)


@pytest.fixture
def import_context():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    dataset = create_owned_dataset(services.datasets, admin.id, "imports", "Imports", "")
    for key, name, data_type, required, unique in (
        ("code", "Code", "string", True, False),
        ("amount", "Amount", "number", True, False),
        ("active", "Active", "boolean", False, False),
        ("day", "Day", "date", False, True),
    ):
        services.dataset_fields.create_field(
            admin.id,
            dataset.key,
            key,
            name,
            data_type,
            is_required=required,
            is_unique=unique,
        )
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code", "amount"))
    return factory, services, admin, dataset


@pytest.fixture
def postgresql_import_context():
    database_url = os.environ.get("REFRNCR_TEST_POSTGRES_URL")
    administration_engine = create_engine(database_url)
    schema = f"record_import_{uuid.uuid4().hex}"
    with administration_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_database_engine(schema_url)
    try:
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        services = create_community_services(factory, ResourceTypeRegistry())
        services.users.bootstrap_organisation()
        admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
        dataset = create_owned_dataset(services.datasets, admin.id, "imports", "Imports", "")
        for key in ("code", "amount"):
            services.dataset_fields.create_field(
                admin.id,
                dataset.key,
                key,
                key.title(),
                "string" if key == "code" else "number",
                is_required=True,
                is_unique=False,
            )
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code", "amount"))
        yield factory, services, admin, dataset
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        administration_engine.dispose()


@pytest.mark.parametrize(
    ("content", "delimiter"),
    [
        (b"code,amount\nA,1\n", "comma"),
        (b"\xef\xbb\xbfcode;amount\nA;1\n", "semicolon"),
        (b"code\tamount\nA\t1\n", "tab"),
        (b'code,amount\n"A\nB",1\n', "comma"),
    ],
)
def test_csv_dialects_bom_quotes_and_embedded_lines(content, delimiter):
    parsed = parse_source("records.csv", content)
    assert parsed.delimiter == delimiter
    assert len(parsed.rows) == 1


def test_csv_headers_shape_empty_rows_and_false_formats():
    with pytest.raises(ImportValidationError, match="header"):
        parse_source("bad.csv", b"code, \nA,1\n")
    with pytest.raises(ImportValidationError, match="unique"):
        parse_source("bad.csv", b"Code, code \nA,1\n")
    with pytest.raises(ImportValidationError, match="column count"):
        parse_source("bad.csv", b"code,amount\nA\n")
    with pytest.raises(ImportValidationError, match="save a new .xlsx copy"):
        parse_source("fake.xlsx", b"code,amount\nA,1\n")
    parsed = parse_source("rows.csv", b"code,amount\n,\nA,1\n")
    assert parsed.ignored_empty_rows == 1
    assert parsed.rows[0].number == 3


def workbook_bytes(*, formula=False, hidden_only=False, second=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Visible <sheet>"
    sheet.append(["code", "amount"])
    sheet.append(["A", "=1+1" if formula else 1])
    if second or hidden_only:
        workbook.create_sheet("Second")
    stream = io.BytesIO()
    workbook.save(stream)
    content = stream.getvalue()
    if not hidden_only:
        return content
    source = zipfile.ZipFile(io.BytesIO(content))
    hidden = io.BytesIO()
    with source, zipfile.ZipFile(hidden, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            value = source.read(entry)
            if entry.filename == "xl/workbook.xml":
                value = value.replace(b'state="visible"', b'state="hidden"')
            target.writestr(entry, value)
    return hidden.getvalue()


def workbook_with_ignored_formula():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["code", "amount", "calculation"])
    sheet.append(["A", 1, "=40+2"])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def workbook_with_cached_formula():
    content = workbook_bytes(formula=True)
    source = zipfile.ZipFile(io.BytesIO(content))
    cached = io.BytesIO()
    with source, zipfile.ZipFile(cached, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            value = source.read(entry)
            if entry.filename == "xl/worksheets/sheet1.xml":
                value = value.replace(b"<v></v>", b"<v>7</v>")
            target.writestr(entry, value)
    return cached.getvalue()


def workbook_without_dimension_metadata():
    content = workbook_bytes()
    source = zipfile.ZipFile(io.BytesIO(content))
    dimensionless = io.BytesIO()
    with source, zipfile.ZipFile(dimensionless, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            value = source.read(entry)
            if entry.filename == "xl/worksheets/sheet1.xml":
                start = value.index(b"<dimension")
                end = value.index(b"/>", start) + 2
                value = value[:start] + value[end:]
            target.writestr(entry, value)
    return dimensionless.getvalue()


def workbook_with_short_row():
    """A row without trailing cells, as exporters that omit worksheet dimensions write it."""
    content = workbook_without_dimension_metadata()
    source = zipfile.ZipFile(io.BytesIO(content))
    short = io.BytesIO()
    with source, zipfile.ZipFile(short, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            value = source.read(entry)
            if entry.filename == "xl/worksheets/sheet1.xml":
                value = re.sub(rb'<c r="B2".*?(?:/>|</c>)', b"", value)
                assert b'r="B2"' not in value
            target.writestr(entry, value)
    return short.getvalue()


def test_xlsx_visible_sheet_selection_and_formula_detection():
    content = workbook_bytes(second=True)
    inspected = parse_source("records.xlsx", content)
    assert inspected.worksheets == ("Visible <sheet>", "Second")
    parsed = parse_source("records.xlsx", content, "Visible <sheet>")
    assert parsed.rows[0].values == ("A", "1")
    formula = parse_source("records.xlsx", workbook_bytes(formula=True), "Visible <sheet>")
    assert formula.rows[0].formulas == frozenset({1})
    cached = parse_source("records.xlsx", workbook_with_cached_formula(), "Visible <sheet>")
    assert cached.rows[0].formulas == frozenset({1})
    assert cached.rows[0].values[1] == ""
    dimensionless = parse_source(
        "records.xlsx", workbook_without_dimension_metadata(), "Visible <sheet>"
    )
    assert dimensionless.headers == ("code", "amount")
    assert dimensionless.rows[0].values == ("A", "1")
    # REF: a short row keeps its missing trailing columns empty instead of failing the upload.
    short = parse_source("records.xlsx", workbook_with_short_row(), "Visible <sheet>")
    assert short.headers == ("code", "amount")
    assert short.rows[0].values == ("A", "")
    assert short.rows[0].formulas == frozenset()
    with pytest.raises(ImportValidationError, match="visible"):
        parse_source("records.xlsx", workbook_bytes(hidden_only=True))
    with pytest.raises(ImportValidationError, match="modern Excel"):
        parse_source("records.xlsx", b"PK\x03\x04broken")


def test_xlsx_zip_bomb_ratio_is_rejected():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "x" * (MAX_COMPRESSION_RATIO * 1000))
    with pytest.raises(ImportValidationError, match="compression ratio"):
        parse_source("records.xlsx", stream.getvalue())


@pytest.mark.parametrize(
    "unsafe_name",
    ["../escape.xml", "xl/externalLinks/externalLink1.xml", "xl/connections.xml"],
)
def test_xlsx_unsafe_internal_content_is_rejected(unsafe_name):
    original = zipfile.ZipFile(io.BytesIO(workbook_bytes()))
    changed = io.BytesIO()
    with original, zipfile.ZipFile(changed, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in original.infolist():
            target.writestr(entry, original.read(entry))
        target.writestr(unsafe_name, b"<xml/>")
    with pytest.raises(ImportValidationError, match="unsafe|macros"):
        parse_source("records.xlsx", changed.getvalue())


def test_xlsx_entry_expansion_and_physical_row_limits(monkeypatch):
    too_many = io.BytesIO()
    with zipfile.ZipFile(too_many, "w") as archive:
        for index in range(1001):
            archive.writestr(f"entry-{index}", b"x")
    with pytest.raises(ImportValidationError, match="too many internal files"):
        parse_source("records.xlsx", too_many.getvalue())

    monkeypatch.setattr(record_import_module, "MAX_UNCOMPRESSED_BYTES", 10)
    expanded = io.BytesIO()
    with zipfile.ZipFile(expanded, "w") as archive:
        archive.writestr("[Content_Types].xml", os.urandom(11))
    with pytest.raises(ImportValidationError, match="too large when expanded"):
        parse_source("records.xlsx", expanded.getvalue())

    monkeypatch.setattr(record_import_module, "MAX_UNCOMPRESSED_BYTES", 100 * 1024 * 1024)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["code"])
    sheet.cell(row=100_002, column=1, value="x")
    content = io.BytesIO()
    workbook.save(content)
    with pytest.raises(ImportValidationError, match="physical rows"):
        parse_source("records.xlsx", content.getvalue(), "Sheet")


def test_mapping_preview_conflicts_atomic_confirmation_and_replay(import_context):
    factory, services, admin, dataset = import_context
    importer = services.record_imports
    content = (
        b"Code,Amount,Active,Day,extra\n A ,1.00,true,2024-01-01,no\nB,2,false,2024-01-02,no\n"
    )
    state, source, assistance = importer.begin(
        admin.id, "session-one", dataset.key, "../safe<script>.csv", content
    )
    assert state.filename == "safe<script>.csv"
    assert assistance.mapping == ("code", "amount", "active", "day", None)
    assert assistance.completeness.unmapped_columns == 0
    assert assistance.completeness.ignored_columns == 1
    assert assistance.columns[0].candidates[0].confidence == "High"
    preview = importer.preview(
        admin.id,
        "session-one",
        dataset.key,
        state.token,
        ("code", "amount", "active", "day", None),
    )
    assert preview.valid
    assert preview.source.delimiter == "comma"
    assert importer.confirm(admin.id, "session-one", dataset.key, state.token) == 2
    with pytest.raises(NotFoundError):
        importer.confirm(admin.id, "session-one", dataset.key, state.token)
    with factory() as session:
        assert session.scalar(select(func.count(DatasetRecord.id))) == 2
        assert session.scalar(select(func.count()).select_from(DatasetRecordValue)) == 8
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.object_type == "dataset",
                    AuditEvent.action == "dataset.records_imported",
                )
            )
            == 1
        )

    duplicate, _, _ = importer.begin(
        admin.id,
        "session-one",
        dataset.key,
        "duplicate.csv",
        b"code,amount\nA,1\nA,1.0\n",
    )
    conflict = importer.preview(
        admin.id, "session-one", dataset.key, duplicate.token, ("code", "amount")
    )
    assert not conflict.valid
    assert conflict.invalid_rows == 2
    assert any("already exists" in error.message for error in conflict.errors)


def test_mapping_table_uses_existing_preview_and_atomic_import_flow(import_context):
    factory, services, admin, _dataset = import_context
    mapping = create_owned_dataset(
        services.datasets,
        admin.id,
        "cost-centers",
        "Cost centers",
        "",
        kind="mapping",
        mapping_mode="standalone",
        source_system="SAP",
        target_system="Salesforce",
    )
    for key, name, role in (
        ("source_value", "Source value", "source_identifier"),
        ("target_value", "Target value", "mapping_target"),
        ("target_name", "Target name", "mapping_target"),
        ("mapping_status", "Status", "mapping_target"),
    ):
        services.dataset_fields.create_field(
            admin.id,
            mapping.key,
            key,
            name,
            "string",
            mapping_role=role,
        )
    content = (
        b"source_value,target_value,target_name,mapping_status\n"
        b"1001,EMEA-SALES,EMEA Sales,mapped\n"
        b"1003,,,unmapped\n"
    )

    state, _source, assistance = services.record_imports.begin(
        admin.id, "mapping-import", mapping.key, "mapping.csv", content
    )
    assert assistance.mapping == (
        "source_value",
        "target_value",
        "target_name",
        "mapping_status",
    )
    preview = services.record_imports.preview(
        admin.id,
        "mapping-import",
        mapping.key,
        state.token,
        assistance.mapping,
    )
    assert preview.valid
    assert (
        services.record_imports.confirm(admin.id, "mapping-import", mapping.key, state.token) == 2
    )

    with factory() as session:
        assert (
            session.scalar(
                select(func.count(DatasetRecord.id)).where(DatasetRecord.dataset_id == mapping.id)
            )
            == 2
        )


def test_manual_mapping_and_ignored_choice_survive_validation_errors(import_context):
    _, services, admin, dataset = import_context
    state, _, assistance = services.record_imports.begin(
        admin.id,
        "manual",
        dataset.key,
        "manual.csv",
        b"mystery,amount,extra\nmanual,bad,ignored\n",
    )
    assert assistance.mapping == (None, "", None)
    assert assistance.columns[1].candidates[0].confidence == "Low"
    preview = services.record_imports.preview(
        admin.id,
        "manual",
        dataset.key,
        state.token,
        ("code", "amount", None),
    )
    assert not preview.valid
    _state, _source, retained = services.record_imports.mapping_assistance(
        admin.id, "manual", dataset.key, state.token
    )
    assert retained.mapping == ("code", "amount", None)
    assert retained.completeness.ignored_columns == 1
    assert retained.completeness.unmapped_columns == 0

    duplicate = services.record_imports.preview(
        admin.id,
        "manual",
        dataset.key,
        state.token,
        ("code", "code", None),
    )
    assert duplicate.non_row_errors
    assert services.record_imports.mapping_assistance(admin.id, "manual", dataset.key, state.token)[
        2
    ].mapping == ("code", "code", None)


def test_mapping_rules_formula_schema_change_and_token_bindings(import_context):
    factory, services, admin, dataset = import_context
    importer = services.record_imports
    content = workbook_bytes(formula=True)
    state, _, _ = importer.begin(admin.id, "one", dataset.key, "records.xlsx", content)
    preview = importer.preview(admin.id, "one", dataset.key, state.token, ("code", "amount"))
    assert not preview.valid
    assert preview.errors[0].row == 2
    assert any("Formula" in error.message for error in preview.errors)
    with pytest.raises(ImportValidationError, match="no longer valid"):
        importer.confirm(admin.id, "one", dataset.key, state.token)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetRecord)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "dataset.records_imported")
            )
            == 0
        )
    with pytest.raises(NotFoundError):
        importer.preview(admin.id, "other", dataset.key, state.token, ("code", "amount"))

    csv_state, _, _ = importer.begin(
        admin.id, "one", dataset.key, "valid.csv", b"code,amount\nZ,9\n"
    )
    assert not importer.preview(
        admin.id, "one", dataset.key, csv_state.token, ("code", "code")
    ).valid
    importer.preview(admin.id, "one", dataset.key, csv_state.token, ("code", "amount"))
    services.dataset_fields.create_field(
        admin.id, dataset.key, "new", "New", "string", is_required=False, is_unique=False
    )
    with pytest.raises(ImportValidationError, match="schema changed"):
        importer.confirm(admin.id, "one", dataset.key, csv_state.token)


def test_formula_in_ignored_column_is_never_exposed_or_persisted(import_context):
    factory, services, admin, dataset = import_context
    state, _, _ = services.record_imports.begin(
        admin.id,
        "ignored-formula",
        dataset.key,
        "records.xlsx",
        workbook_with_ignored_formula(),
    )
    preview = services.record_imports.preview(
        admin.id,
        "ignored-formula",
        dataset.key,
        state.token,
        ("code", "amount", None),
    )
    assert preview.valid
    assert all("40+2" not in value for row in preview.sample for value in row)
    services.record_imports.confirm(admin.id, "ignored-formula", dataset.key, state.token)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetRecordValue)) == 2


def test_expired_transient_token(import_context):
    _, services, admin, dataset = import_context
    now = [10.0]
    importer = DatasetRecordImportService(
        services.dataset_records, TransientImportStore(clock=lambda: now[0], ttl=5)
    )
    state, _, _ = importer.begin(admin.id, "one", dataset.key, "valid.csv", b"code,amount\nZ,9\n")
    now[0] = 16.0
    with pytest.raises(NotFoundError):
        importer.preview(admin.id, "one", dataset.key, state.token, ("code", "amount"))


def test_staged_import_activity_slides_expiry_and_idle_import_expires(import_context):
    _, services, admin, dataset = import_context
    now = [0.0]
    importer = DatasetRecordImportService(
        services.dataset_records, TransientImportStore(clock=lambda: now[0], ttl=900)
    )
    state, _, _ = importer.begin(
        admin.id, "active", dataset.key, "valid.csv", b"code,amount\nZ,9\n"
    )
    now[0] = 840
    touched = importer.state(admin.id, "active", dataset.key, state.token)
    assert touched.expires_at == 1740
    now[0] = 1500
    assert importer.state(admin.id, "active", dataset.key, state.token).token == state.token
    now[0] = 2400
    with pytest.raises(NotFoundError):
        importer.state(admin.id, "active", dataset.key, state.token)


def test_preview_error_cap_authorization_and_session_revocation(import_context):
    _, services, admin, dataset = import_context
    viewer = services.users.create_user(admin.id, "viewer@example.com", "Viewer", PASSWORD)
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    with pytest.raises(NotFoundError):
        services.record_imports.begin(
            viewer.id, "viewer-session", dataset.key, "records.csv", b"code,amount\nA,1\n"
        )
    rows = "".join(f"row-{index},bad\n" for index in range(120))
    state, _, _ = services.record_imports.begin(
        admin.id,
        "admin-session",
        dataset.key,
        "errors.csv",
        f"code,amount\n{rows}".encode(),
    )
    preview = services.record_imports.preview(
        admin.id,
        "admin-session",
        dataset.key,
        state.token,
        ("code", "amount"),
        {"amount": {"profile_id": "v1-plain"}},
    )
    assert len(preview.errors) == 100
    assert preview.total_errors == 120
    assert preview.invalid_rows == 120
    assert preview.row_report_truncated
    services.record_imports.revoke_session("admin-session")
    with pytest.raises(NotFoundError):
        services.record_imports.preview(
            admin.id, "admin-session", dataset.key, state.token, ("code", "amount")
        )


@pytest.mark.parametrize("failed_rows", [1, 99, 100, 101])
def test_failed_row_report_groups_all_errors_and_limits_rows(import_context, failed_rows):
    _, services, admin, dataset = import_context
    rows = "".join(",bad\n" for _ in range(failed_rows))
    state, _, _ = services.record_imports.begin(
        admin.id, "report", dataset.key, "errors.csv", f"code,amount\n{rows}".encode()
    )
    preview = services.record_imports.preview(
        admin.id,
        "report",
        dataset.key,
        state.token,
        ("code", "amount"),
        {"amount": {"profile_id": "v1-plain"}},
    )
    assert preview.invalid_rows == failed_rows
    assert len(preview.row_error_groups) == min(failed_rows, 100)
    assert all(len(errors) == 2 for _, errors in preview.row_error_groups)
    assert (
        preview.row_error_groups
        == services.record_imports.saved_preview(
            admin.id, "report", dataset.key, state.token
        ).row_error_groups
    )
    assert preview.row_report_truncated is (failed_rows > 100)
    if failed_rows >= 100:
        assert preview.row_error_groups[-1][0] == 101
    if failed_rows == 101:
        assert [row for row, _ in preview.row_error_groups] == list(range(2, 102))
        assert all(row != 102 for row, _ in preview.row_error_groups)


def test_mapping_errors_are_non_row_errors_and_do_not_consume_row_limit(import_context):
    _, services, admin, dataset = import_context
    state, _, _ = services.record_imports.begin(
        admin.id, "mapping", dataset.key, "records.csv", b"code,amount\nA,1\n"
    )
    preview = services.record_imports.preview(
        admin.id, "mapping", dataset.key, state.token, ("code", "code")
    )
    assert preview.invalid_rows == 0
    assert preview.row_error_groups == ()
    assert preview.non_row_errors == preview.errors
    assert not preview.row_report_truncated


def test_wrong_token_binding_does_not_consume_other_sessions_import(import_context):
    _, services, admin, dataset = import_context
    state, _, _ = services.record_imports.begin(
        admin.id, "owner", dataset.key, "records.csv", b"code,amount\nA,1\n"
    )
    services.record_imports.preview(admin.id, "owner", dataset.key, state.token, ("code", "amount"))
    with pytest.raises(NotFoundError):
        services.record_imports.confirm(admin.id, "attacker", dataset.key, state.token)
    assert services.record_imports.confirm(admin.id, "owner", dataset.key, state.token) == 1


def test_process_local_worker_mismatch_fails_closed_without_consuming(import_context):
    _, services, admin, dataset = import_context
    first = services.record_imports
    second = DatasetRecordImportService(services.dataset_records)
    state, _, _ = first.begin(
        admin.id, "session", dataset.key, "records.csv", b"code,amount\nA,1\n"
    )
    first.preview(admin.id, "session", dataset.key, state.token, ("code", "amount"))
    with pytest.raises(NotFoundError):
        second.saved_preview(admin.id, "session", dataset.key, state.token)
    assert first.confirm(admin.id, "session", dataset.key, state.token) == 1


def test_cancel_and_double_consume_cleanup_are_isolated():
    store = TransientImportStore()
    first = store.create("actor", "one", "dataset", "one.csv", b"one", "csv")
    second = store.create("actor", "two", "dataset", "two.csv", b"two", "csv")
    store.cancel(first.token, "actor", "one", "dataset")
    with pytest.raises(NotFoundError):
        store.get(first.token, "actor", "one", "dataset")
    assert store.consume(second.token, "actor", "two", "dataset") == second
    with pytest.raises(NotFoundError):
        store.consume(second.token, "actor", "two", "dataset")


def test_transient_capacity_is_atomic_and_does_not_evict_existing_state():
    store = TransientImportStore(max_bytes=10, capacity=10)
    barrier = Barrier(2)

    def upload(session):
        barrier.wait()
        try:
            return store.create("actor", session, "dataset", "file.csv", b"123456", "csv")
        except ImportValidationError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(upload, ("one", "two")))
    states = [result for result in results if not isinstance(result, Exception)]
    assert len(states) == 1
    assert sum(isinstance(result, ImportValidationError) for result in results) == 1
    touched = store.get(states[0].token, "actor", states[0].session_id, "dataset")
    assert touched.expires_at > states[0].expires_at


@pytest.mark.parametrize("failure_target", ["values", "audit"])
def test_unexpected_late_persistence_failures_roll_back_entire_import(
    import_context, failure_target
):
    factory, services, admin, dataset = import_context
    state, _, _ = services.record_imports.begin(
        admin.id, "failure", dataset.key, "records.csv", b"code,amount\nA,1\nB,2\n"
    )
    assert services.record_imports.preview(
        admin.id, "failure", dataset.key, state.token, ("code", "amount")
    ).valid
    base_factory = services.record_imports.uow

    class FailingContext:
        def __init__(self, inner, write):
            self.inner = inner
            self.write = write

        def __enter__(self):
            uow = self.inner.__enter__()
            if not self.write:
                return uow
            if failure_target == "values":
                original = uow.dataset_records.add_values
                calls = 0

                def fail_values(record, values):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("injected value failure")
                    return original(record, values)

                uow.dataset_records.add_values = fail_values
            else:
                original = uow.add_audit
                calls = 0

                def fail_audit(**values):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise RuntimeError("injected audit failure")
                    return original(**values)

                uow.add_audit = fail_audit
            return uow

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

    def failing_factory(*, write=False, serialized=False):
        return FailingContext(base_factory(write=write, serialized=serialized), write)

    services.record_imports.uow = failing_factory
    with pytest.raises(RuntimeError, match="injected"):
        services.record_imports.confirm(admin.id, "failure", dataset.key, state.token)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetRecord)) == 0
        assert session.scalar(select(func.count()).select_from(DatasetRecordValue)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "dataset.records_imported")
            )
            == 0
        )


def test_second_conflicting_import_is_fully_rejected(import_context):
    factory, services, admin, dataset = import_context
    tokens = []
    for session in ("first", "second"):
        state, _, _ = services.record_imports.begin(
            admin.id, session, dataset.key, "records.csv", b"code,amount\nA,1\nB,2\n"
        )
        assert services.record_imports.preview(
            admin.id, session, dataset.key, state.token, ("code", "amount")
        ).valid
        tokens.append((session, state.token))
    assert services.record_imports.confirm(admin.id, tokens[0][0], dataset.key, tokens[0][1]) == 2
    with pytest.raises(ImportValidationError, match="no longer valid"):
        services.record_imports.confirm(admin.id, tokens[1][0], dataset.key, tokens[1][1])
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetRecord)) == 2


def test_browser_import_flow_csrf_visibility_and_escaping(import_context):
    _, services, admin, dataset = import_context
    for key in ("active", "day"):
        services.dataset_fields.delete_field(admin.id, dataset.key, key)
    app = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
        dataset_records=services.dataset_records,
        record_imports=services.record_imports,
    )
    client = app.test_client()
    login = client.get("/login", base_url="https://localhost")
    csrf = login.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    client.post(
        "/login",
        base_url="https://localhost",
        follow_redirects=True,
        data={"email": admin.email, "password": PASSWORD, "csrf_token": csrf},
    )
    upload = client.get(f"/datasets/{dataset.key}/records/import", base_url="https://localhost")
    csrf = upload.get_data(as_text=True).split("name='csrf_token' value='")[1].split("'", 1)[0]
    assert (
        client.post(
            f"/datasets/{dataset.key}/records/import",
            base_url="https://localhost",
            follow_redirects=True,
            data={"file": (io.BytesIO(b"code,amount\nX,3\n"), "<file>.csv")},
        ).status_code
        == 403
    )
    unreadable = client.post(
        f"/datasets/{dataset.key}/records/import",
        base_url="https://localhost",
        follow_redirects=True,
        data={
            "csrf_token": csrf,
            "file": (io.BytesIO(b"PK\x03\x04broken"), "broken.xlsx"),
        },
    )
    assert unreadable.status_code == 400
    assert "save a new .xlsx copy" in unreadable.get_data(as_text=True)
    assert "role='alert'" in unreadable.get_data(as_text=True)
    mapping = client.post(
        f"/datasets/{dataset.key}/records/import",
        base_url="https://localhost",
        follow_redirects=True,
        data={
            "csrf_token": csrf,
            "file": (io.BytesIO(b"code,amount\n<script>,3\n"), "<file>.csv"),
        },
    )
    body = mapping.get_data(as_text=True)
    assert mapping.status_code == 200
    assert "&lt;file&gt;.csv" in body
    assert "High confidence" in body
    assert "Ready to continue · 2 fields mapped, 0 columns not imported" in body
    assert "Examples:" in body
    assert "&lt;script&gt;" in body
    assert "internal score" not in body.casefold()
    token = mapping.request.path.split("/import/")[1].split("/step/")[0]
    malformed_mapping = client.post(
        f"/datasets/{dataset.key}/records/import/{token}/preview",
        base_url="https://localhost",
        follow_redirects=True,
        data={"csrf_token": csrf, "mapping_0": "code"},
    )
    assert malformed_mapping.status_code == 400
    assert "exactly one mapping" in malformed_mapping.get_data(as_text=True)
    incomplete = client.post(
        f"/datasets/{dataset.key}/records/import/{token}/preview",
        base_url="https://localhost",
        follow_redirects=True,
        data={"csrf_token": csrf, "mapping_0": "code", "mapping_1": ""},
    )
    assert incomplete.status_code == 400
    incomplete_body = incomplete.get_data(as_text=True)
    assert "1 field not mapped:" in incomplete_body
    assert "<code class='mapping-field-name'>Amount</code>" in incomplete_body
    reopened = client.get(
        f"/datasets/{dataset.key}/records/import/{token}/mapping",
        base_url="https://localhost",
        follow_redirects=True,
    )
    assert reopened.status_code == 200
    assert "value='code' selected" in reopened.get_data(as_text=True)
    preview = client.post(
        f"/datasets/{dataset.key}/records/import/{token}/preview",
        base_url="https://localhost",
        follow_redirects=True,
        data={"csrf_token": csrf, "mapping_0": "code", "mapping_1": "amount"},
    )
    assert preview.status_code == 200
    assert "&lt;script&gt;" in preview.get_data(as_text=True)
    final = client.get(
        f"/datasets/{dataset.key}/records/import/{token}/step/4", base_url="https://localhost"
    )
    assert final.status_code == 200
    confirmed = client.post(
        f"/datasets/{dataset.key}/records/import/{token}/confirm",
        base_url="https://localhost",
        follow_redirects=True,
        data={
            "csrf_token": csrf,
            "confirmation": re.search("name='confirmation' value='([^']+)'", final.text)[1],
        },
    )
    assert confirmed.status_code == 200
    assert "Added: 1" in confirmed.get_data(as_text=True)

    from datetime import UTC, datetime

    services.dataset_versions.publish(
        admin.id,
        dataset.id,
        datetime.now(UTC).date(),
        "Initial import",
        services.datasets.get_dataset_for_edit(admin.id, dataset.key).revision,
    )
    services.dataset_versions.create_concept(admin.id, dataset.id)

    error_rows = b"code,amount\n" + b"not-a-code,not-a-number\n" * 101
    mapping = client.post(
        f"/datasets/{dataset.key}/records/import",
        base_url="https://localhost",
        follow_redirects=True,
        data={"csrf_token": csrf, "file": (io.BytesIO(error_rows), "errors.csv")},
    )
    token = mapping.request.path.split("/import/")[1].split("/step/")[0]
    error_preview = client.post(
        f"/datasets/{dataset.key}/records/import/{token}/preview",
        base_url="https://localhost",
        follow_redirects=True,
        data={
            "csrf_token": csrf,
            "mapping_0": "code",
            "mapping_1": "amount",
            "source_0": "v1-text",
            "source_1": "v1-plain",
            "timezone_0": "",
            "timezone_1": "",
            "fold_0": "",
            "fold_1": "",
        },
    )
    error_body = error_preview.get_data(as_text=True)
    assert error_preview.status_code == 200
    assert "Failing data row 100 — source file row 101" in error_body
    assert "source file row 102" not in error_body
    assert "Showing 100 of 101 failing data rows" in error_body


@pytest.mark.postgres
def test_postgresql_import_lock_constraints_and_manual_create_race(postgresql_import_context):
    factory, services, admin, dataset = postgresql_import_context
    state, _, _ = services.record_imports.begin(
        admin.id, "session", dataset.key, "records.csv", b"code,amount\nA,1\nB,2\n"
    )
    preview = services.record_imports.preview(
        admin.id, "session", dataset.key, state.token, ("code", "amount")
    )
    assert preview.valid
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "B", "amount": "2"})
    with pytest.raises(ImportValidationError, match="no longer valid"):
        services.record_imports.confirm(admin.id, "session", dataset.key, state.token)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetRecord)) == 1


@pytest.mark.postgres
def test_postgresql_same_token_concurrent_confirmation(postgresql_import_context, monkeypatch):
    from threading import Event

    factory, services, admin, dataset = postgresql_import_context
    importer = services.record_imports
    state, _, _ = importer.begin(
        admin.id, "parallel", dataset.key, "parallel.csv", b"code,amount\nA,1\nB,2\n"
    )
    importer.preview(admin.id, "parallel", dataset.key, state.token, ("code", "amount"))
    importer.navigate(admin.id, "parallel", dataset.key, state.token, 4)
    claimed, release = Event(), Event()
    original = importer.store.claim

    def paused_claim(state):
        result = original(state)
        claimed.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(importer.store, "claim", paused_claim)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            importer.confirm,
            admin.id,
            "parallel",
            dataset.key,
            state.token,
            require_confirmation=True,
        )
        assert claimed.wait(10)
        try:
            second = executor.submit(
                importer.confirm,
                admin.id,
                "parallel",
                dataset.key,
                state.token,
                require_confirmation=True,
            )
            with pytest.raises(NotFoundError):
                second.result(timeout=5)
        finally:
            release.set()
        assert first.result(timeout=10) == 2
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetRecord)) == 2
        assert session.scalar(select(func.count()).select_from(DatasetRecordValue)) == 4
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_type == "dataset",
                    AuditEvent.action == "dataset.records_imported",
                )
            )
            == 1
        )
