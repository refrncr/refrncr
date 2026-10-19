"""REF-154 transaction, security, full-file and browser response regressions."""

import io
from datetime import datetime, time

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from support import html_elements

from refrncr.models import AuditEvent, DatasetFieldSourceFormat, DatasetRecord
from refrncr.record_import import ImportValidationError, TransientImportStore, parse_source
from refrncr.services import NotFoundError


@pytest.fixture
def context(import_context):
    return import_context


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def database_context(request):
    fixture = "import_context" if request.param == "sqlite" else "postgresql_import_context"
    return request.getfixturevalue(fixture)


def start(context, value="1.234,56", session="source-formats", code="A"):
    _, services, admin, dataset = context
    state, _, _ = services.record_imports.begin(
        admin.id, session, dataset.key, "values.csv", f"code;amount\n{code};{value}\n".encode()
    )
    return state


def preview(context, state, choice=None, session="source-formats"):
    _, services, admin, dataset = context
    return services.record_imports.preview(
        admin.id,
        session,
        dataset.key,
        state.token,
        ("code", "amount"),
        None if choice is None else {"amount": choice},
    )


def saved(context):
    factory, _, _, _ = context
    with factory() as session:
        return {
            p.field_id: dict(p.choice) for p in session.scalars(select(DatasetFieldSourceFormat))
        }


def confirm(context, state, session="source-formats"):
    _, services, admin, dataset = context
    return services.record_imports.confirm(admin.id, session, dataset.key, state.token)


def test_full_confirmation_remembers_and_preview_uses_canonical_values(context):
    factory, services, admin, dataset = context
    state = start(context)
    proposal = services.record_imports.format_assistance(
        admin.id, "source-formats", dataset.key, state.token
    )
    assert proposal[1]["choice"]["profile_id"] == "v1-eu"
    result = preview(context, state)
    assert result.valid and result.sample == (("A", "1234.56"),)
    assert saved(context) == {}
    assert confirm(context, state) == 1
    assert {p["profile_id"] for p in saved(context).values()} == {"v1-text", "v1-eu"}
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetRecord)) == 1
        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.action == "dataset.records_imported")
            )
        )
        assert len(events) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.object_type == "dataset_record")
            )
            == 0
        )
    state = start(context, "1.234", code="B")
    proposal = services.record_imports.format_assistance(
        admin.id, "source-formats", dataset.key, state.token
    )
    assert proposal[1]["choice"]["profile_id"] == "v1-eu"
    assert preview(context, state, {"profile_id": "v1-plain"}).sample == (("B", "1.234"),)
    assert confirm(context, state) == 1
    assert "v1-plain" in {p["profile_id"] for p in saved(context).values()}
    with pytest.raises(NotFoundError):
        confirm(context, state)


@pytest.mark.parametrize("end", ["cancel", "expiry", "validation", "rollback", "stale"])
def test_unsuccessful_paths_do_not_remember(database_context, monkeypatch, end):
    _, services, admin, dataset = database_context
    clock = [0.0]
    services.record_imports.store = TransientImportStore(clock=lambda: clock[0])
    state = start(database_context)
    assert preview(database_context, state).valid
    if end == "cancel":
        services.record_imports.cancel(admin.id, "source-formats", dataset.key, state.token)
    elif end == "expiry":
        clock[0] = 901
        with pytest.raises(NotFoundError):
            confirm(database_context, state)
    elif end == "validation":
        assert not preview(database_context, state, {"profile_id": "v1-us"}).valid
    elif end == "stale":
        services.dataset_records.create_record(admin.id, dataset.key, {"code": "Z", "amount": "5"})
        with pytest.raises(Exception, match="changed"):
            confirm(database_context, state)
    else:
        import refrncr.record_import as module

        def fail(*args, **kwargs):
            raise RuntimeError("injected audit failure")

        with monkeypatch.context() as patch:
            patch.setattr(module, "record_audit", fail)
            with pytest.raises(RuntimeError, match="injected"):
                confirm(database_context, state)
        assert saved(database_context) == {}
        assert confirm(database_context, state) == 1
        assert saved(database_context)
        return
    assert saved(database_context) == {}


def test_full_file_detection_and_validation_beyond_display_examples(context):
    _, services, admin, dataset = context
    content = "code;amount\n" + "".join(f"row{i};1.234,56\n" for i in range(150)) + "bad;1,234.56\n"
    state, _, _ = services.record_imports.begin(
        admin.id, "source-formats", dataset.key, "full.csv", content.encode()
    )
    assert not preview(context, state).valid
    result = preview(context, state, {"profile_id": "v1-eu"})
    assert result.invalid_rows == 1
    assert result.errors[0].row == 152
    assert not result.valid
    assert saved(context) == {}


def test_excel_native_temporals_survive_staging():
    workbook = Workbook()
    workbook.active.append(["date", "time", "datetime", "blank"])
    workbook.active.append(
        [datetime(2024, 2, 29), time(12, 34, 56), datetime(2024, 2, 29, 15, 45, 12), None]
    )
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    parsed = parse_source("native.xlsx", output.getvalue(), "Sheet")
    row = parsed.rows[0]
    assert row.source_value(0) == datetime(2024, 2, 29)
    assert row.source_value(1) == time(12, 34, 56)
    assert row.source_value(2) == datetime(2024, 2, 29, 15, 45, 12)
    assert row.source_value(3) == ""


def form_data(wizard, profile="v1-eu"):
    return {
        "mapping_0": "code",
        "mapping_1": "amount",
        "source_0": "v1-text",
        "source_1": profile,
        "timezone_0": "",
        "timezone_1": "",
        "fold_0": "",
        "fold_1": "",
    }


def test_mapping_progressive_disclosure_and_live_examples(wizard):
    page = wizard.upload(b"code;amount\nA;1.234\nB;2.345\n")
    assert "Show all formats" in page.text
    assert sum(tag == "details" and "open" in attrs for tag, attrs in html_elements(page.text)) == 1
    assert "Choose how this column is interpreted." in page.text
    assert "Ambiguous source format;" not in page.text
    assert "id='source-1-0'" in page.text
    assert "value='v1-eu'" in page.text
    assert "data-format-error" in page.text
    response = wizard.post(wizard.path + "/formats", form_data(wizard))
    assert response.status_code == 200
    assert "<code>1234</code>" in response.text and "<code>2345</code>" in response.text
    assert (
        sum(
            "source-format-pair" in attrs.get("class", "").split()
            for _, attrs in html_elements(response.text)
        )
        == 2
    )
    response = wizard.post(wizard.path + "/preview", form_data(wizard, "v1-plain"))
    assert "1.234" in response.text and "2.345" in response.text
    assert "Changes in this import" in response.text


def test_incomplete_source_format_components_are_rendered_from_service_issue(wizard):
    wizard.upload(b"code;amount\nA;1.234\nB;2.345\n")
    data = form_data(wizard)
    data["date_1"] = "unexpected-component"
    response = wizard.post(wizard.path + "/formats", data)
    assert response.status_code == 200
    assert "Choose a valid source type and its required format components." in response.text
    assert "source-error-1" in response.text


def test_example_escaping_bounds_and_format_endpoint_csrf_binding(wizard):
    page = wizard.upload(
        ("code;amount\n" + ("<script>" + ("x" * 300) + ";1.234,56\n") * 5).encode()
    )
    assert "<script>" not in page.text
    assert "&lt;script&gt;" in page.text
    data = form_data(wizard)
    response = wizard.post(wizard.path + "/formats", data)
    # The fixture repeats one row five times, so the distinct-value rule shows it once.
    assert (
        sum(
            "source-format-pair" in attrs.get("class", "").split()
            for _, attrs in html_elements(response.text)
        )
        == 1
    )
    assert "<script>" not in response.text
    bad = dict(data, csrf_token="forged")
    assert wizard.post(wizard.path + "/formats", bad).status_code == 403
    assert (
        wizard.post(wizard.path.replace("/import/", "/import/wrong") + "/formats", data).status_code
        == 404
    )
    assert wizard.count() == 0


def test_timezone_and_fold_decisions_remain_in_mapping(import_context):
    _, s, a, d = import_context
    s.dataset_fields.create_field(a.id, d.key, "stamp", "Stamp", "timestamp", False, False)
    state, _, _ = s.record_imports.begin(
        a.id, "time", d.key, "time.csv", b"code;amount;stamp\nA;1;2024-10-27 02:30:00\n"
    )
    choices = {"stamp": {"profile_id": "v1-datetime-ymd-dash-hms", "timezone": "Europe/Amsterdam"}}
    result = s.record_imports.preview(
        a.id, "time", d.key, state.token, ("code", "amount", "stamp"), choices
    )
    assert not result.valid
    assert len(result.non_row_errors) == 1
    assert result.non_row_errors[0].field == "Stamp"
    assert result.non_row_errors[0].code == "source_format_ambiguous"
    assert s.record_imports.state(a.id, "time", d.key, state.token).step == 3
    choices["stamp"]["fold"] = "later"
    result = s.record_imports.preview(
        a.id, "time", d.key, state.token, ("code", "amount", "stamp"), choices
    )
    assert result.valid
    assert result.sample[0][-1] == "2024-10-27T01:30:00Z"


def test_nonlocal_choices_cannot_persist_arbitrary_timezone_content(import_context):
    state = start(import_context)
    with pytest.raises(ImportValidationError, match="local Timestamp"):
        preview(
            import_context,
            state,
            {"profile_id": "v1-eu", "timezone": "arbitrary text", "fold": "ignored"},
        )
    assert saved(import_context) == {}


def test_previewed_format_choices_are_immutable(import_context):
    _, s, a, d = import_context
    state = start(import_context)
    assert preview(import_context, state).valid
    current = s.record_imports.state(a.id, "source-formats", d.key, state.token)
    with pytest.raises(TypeError):
        current.source_formats["amount"]["profile_id"] = "v1-us"
    with pytest.raises(TypeError):
        current.source_formats["amount"] = {"profile_id": "v1-us"}
    assert confirm(import_context, state) == 1


def test_invalid_string_uses_value_error_without_meaningless_format_decision(import_context):
    _, s, a, d = import_context
    state = start(import_context, code="x" * 1001)
    result = preview(import_context, state)
    assert result.invalid_rows == 1
    assert not result.non_row_errors
    columns = s.record_imports.format_assistance(a.id, "source-formats", d.key, state.token)
    assert not columns[0]["applicable"]
    assert columns[0]["choice"]["profile_id"] == "v1-text"
    assert columns[0]["error"]
