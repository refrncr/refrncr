"""REF-134 wizard state and browser regressions, alongside the REF-81 safety tests."""

import io
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from dataset_helpers import create_mapping_dataset, create_owned_dataset
from openpyxl import Workbook
from support import PASSWORD, html_elements, html_tag_texts, html_text

from refrncr.record_import import TransientImportStore
from refrncr.services import NotFoundError


def states(page):
    return re.findall("data-step='([1-4])' data-step-status='([^']+)'", page.text)


def optional_field(wizard, key):
    wizard.services.dataset_fields.create_field(
        wizard.admin.id,
        wizard.dataset.key,
        key,
        key.title(),
        "boolean" if key == "active" else "date",
    )


def workbook(sheets=("First", "Second"), headers=("code", "amount")):
    book = Workbook()
    book.remove(book.active)
    for name in sheets:
        sheet = book.create_sheet(name)
        sheet.append(headers)
        sheet.append(("A", "1"))
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_four_steps_confirm_on_preview_and_replay(wizard):
    assert states(wizard.get(wizard.root)) == [
        ("1", "active"),
        ("2", "remaining"),
        ("3", "remaining"),
        ("4", "remaining"),
    ]
    mapped = wizard.upload()
    assert states(mapped) == [
        ("1", "completed"),
        ("2", "completed"),
        ("3", "active"),
        ("4", "remaining"),
    ]
    assert "Completed automatically" in mapped.text
    assert wizard.get(wizard.path + "/step/2").status_code == 404
    assert wizard.get(wizard.path + "/step/4").status_code == 404
    assert wizard.get(wizard.path + "/step/5").status_code == 404
    assert wizard.post(wizard.path + "/confirm").status_code == 404
    preview = wizard.preview()
    assert states(preview)[3] == ("4", "active")
    assert wizard.count() == 0
    assert wizard.post(wizard.path + "/confirm").status_code == 409
    final = wizard.get(wizard.path + "/step/4")
    assert states(final)[3] == ("4", "active")
    summary = re.sub(r"\s+", " ", html_text(final.text))
    assert "File: records.csv" in summary
    assert "First import: 1 record will be added to the draft." in summary
    assert "Import records" in final.text
    assert wizard.count() == 0
    assert wizard.get(wizard.path + "/step/5").status_code == 404
    success = wizard.post(wizard.path + "/confirm")
    assert "Import results" in success.text and "Added: 1" in success.text
    assert "import_results=1" in success.request.url
    assert wizard.count() == 1
    assert wizard.get(wizard.path + "/step/5").status_code == 404
    assert wizard.post(wizard.path + "/confirm").status_code == 404
    assert (
        wizard.post(
            wizard.path + "/preview", {"mapping_0": "code", "mapping_1": "amount"}
        ).status_code
        == 404
    )
    another_import = wizard.get(wizard.root)
    assert another_import.status_code == 200
    assert "Import records</h1>" in another_import.text


@pytest.mark.parametrize("sheets", [("Only",), ("First", "Second")])
def test_worksheet_skip_or_explicit_selection_and_invalidation(wizard, sheets):
    page = wizard.upload(workbook(sheets), "records.xlsx")
    if len(sheets) == 1:
        assert states(page)[1:3] == [("2", "completed"), ("3", "active")]
        assert wizard.post(wizard.path + "/worksheet", {"worksheet": "Only"}).status_code == 404
    else:
        assert states(page)[1] == ("2", "active")
        assert "value=''" in page.text
        assert wizard.get(wizard.path + "/mapping").status_code == 404
        assert wizard.preview().status_code == 404
        assert wizard.post(wizard.path + "/worksheet", {"worksheet": ""}).status_code == 400
        page = wizard.post(wizard.path + "/worksheet", {"worksheet": "First"})
        assert states(page)[2] == ("3", "active")
        format_review = wizard.post(
            wizard.path + "/formats",
            {
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
        assert format_review.status_code == 200
        wizard.preview()
        wizard.post(wizard.path + "/worksheet", {"worksheet": "First"})
        assert wizard.get(wizard.path + "/step/5").status_code == 404
        changed = wizard.post(wizard.path + "/worksheet", {"worksheet": "Second"})
        assert states(changed)[3:] == [("4", "remaining")]
        assert wizard.get(wizard.path + "/step/5").status_code == 404


def test_back_navigation_saves_choices_and_invalidates_only_dependents(wizard):
    optional_field(wizard, "active")
    wizard.upload(b"mystery,amount,extra\nA,1,true\n")
    wizard.preview(("code", "amount", "active"))
    upload = wizard.get(wizard.path + "/step/1")
    assert "Current file:" in upload.text
    assert wizard.get(wizard.path + "/step/5").status_code == 404
    wizard.get(wizard.path + "/mapping")
    back = wizard.post(
        wizard.path + "/preview",
        {
            "mapping_0": "code",
            "mapping_1": "amount",
            "mapping_2": "__ignored__",
            "go_step": "1",
        },
    )
    assert states(back)[0] == ("1", "active")
    mapped = wizard.get(wizard.path + "/mapping")
    assert "value='__ignored__' selected" in mapped.text
    assert wizard.get(wizard.path + "/step/5").status_code == 404
    assert wizard.count() == 0


@pytest.mark.parametrize(
    "mapping", [("code", "code"), ("code", None), ("unknown", "amount"), ("code", "")]
)
def test_mapping_rules_cannot_be_bypassed(wizard, mapping):
    wizard.upload()
    page = wizard.preview(mapping)
    assert page.status_code == 400
    assert states(page)[2] == ("3", "active")
    assert "data-error-focus" in page.text
    assert wizard.get(wizard.path + "/step/4").status_code == 404
    assert wizard.post(wizard.path + "/confirm").status_code == 404
    assert wizard.count() == 0


@pytest.mark.parametrize(
    "values",
    [
        [("mapping_0", "code")],
        [("mapping_0", "code"), ("mapping_0", "amount"), ("mapping_1", "amount")],
        [("mapping_0", "code"), ("mapping_2", "amount")],
        [("mapping_0", "code"), ("mapping_1", "amount"), ("step", "5")],
    ],
)
def test_crafted_mapping_cardinality(wizard, values):
    wizard.upload()
    assert wizard.post(wizard.path + "/preview", values).status_code == 400
    assert wizard.get(wizard.path + "/step/5").status_code == 404


def test_difference_preview_all_mapped_fields_dataset_order_and_empty_marker(wizard):
    optional_field(wizard, "active")
    optional_field(wizard, "day")
    content = b"day,amount,code,active\n" + b"".join(f",1,C{i},\n".encode() for i in range(12))
    wizard.upload(content)
    preview = wizard.preview(("day", "amount", "code", "active"))
    assert preview.status_code == 200
    table = preview.text.split("<thead>")[1].split("</table>")[0]
    assert re.findall("<th scope='col'>(.*?)</th>", table) == [
        "Identifier",
        "Status",
        "Changed fields",
    ]
    assert re.findall("<dt>(.*?)</dt>", preview.text)[:4] == ["Code", "Amount", "Active", "Day"]
    assert "This record will be added." in html_text(preview.text)
    assert sum(tag == "tr" for tag, _ in html_elements(table)) == 13
    assert "C9" in table and "C10" in table
    assert "—" in table
    assert "No value" in preview.text
    assert "First import: 12 records will be added to the draft." in re.sub(
        r"\s+", " ", html_text(preview.text)
    )
    assert "Page 1 of 1" not in preview.text
    assert wizard.count() == 0


@pytest.mark.parametrize("count", [1, 99, 100, 101])
def test_error_summary_and_capped_accessible_modal(wizard, count):
    content = b"code,amount\n" + b"<script>,bad<script>\n" * count
    wizard.upload(content, "<file>.csv")
    page = wizard.post(
        wizard.path + "/preview",
        {
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
    outside = re.sub(r"<dialog\b.*?</dialog>", "", page.text, flags=re.S)
    dialog = re.search(r"<dialog\b.*?</dialog>", page.text, re.S)[0]
    assert f"{count} source rows failed validation" in outside
    assert "No records have been imported." in outside
    assert "View error report" in outside
    assert "Failing data row" not in outside
    assert "<table>" not in outside
    assert sum(tag == "section" for tag, _ in html_elements(dialog)) == min(100, count)
    assert f"Failing data row {min(100, count)} — source file row {min(100, count) + 1}" in dialog
    assert ("Showing 100 of 101" in dialog) == (count == 101)
    assert "aria-labelledby=" in dialog and "aria-describedby=" in dialog
    assert "data-dialog-close" in dialog
    assert "bad&lt;script&gt;" in dialog and "Identifier: &lt;script&gt;" in dialog
    assert "<script>" not in dialog
    assert wizard.get(wizard.path + "/step/5").status_code == 404
    assert wizard.count() == 0


def test_resume_cancel_expiry_and_new_session(wizard):
    clock = [10.0]
    wizard.services.record_imports.store = TransientImportStore(clock=lambda: clock[0])
    wizard.upload()
    wizard.preview()
    wizard.get(wizard.path + "/step/1")
    assert states(wizard.get(wizard.root))[3] == ("4", "active")
    resumed = wizard.get(wizard.root).text
    assert "Changes in this import" in resumed and "First import: 1 record will be added" in resumed
    assert "Upload corrected file" not in resumed
    assert "You can return to this import" not in resumed
    assert wizard.post(wizard.path + "/cancel").status_code == 200
    assert wizard.get(wizard.path + "/step/4").status_code == 404
    assert states(wizard.get(wizard.root))[0] == ("1", "active")
    wizard.upload()
    wizard.preview()
    clock[0] += 901
    expired = wizard.get(wizard.path + "/step/4")
    assert expired.status_code == 410
    assert "expired after 15 minutes without activity" in expired.text
    assert "Upload the file again" in expired.text
    assert states(wizard.get(wizard.root))[0] == ("1", "active")
    wizard.upload()
    wizard.preview()
    wizard.post("/logout")
    assert not wizard.services.record_imports.store._states


def test_busy_cancel_timeout_is_readable_and_preserves_the_import(wizard):
    store = TransientImportStore(cancel_wait_seconds=0.01)
    wizard.services.record_imports.store = store
    wizard.upload()
    state = store._states[wizard.token]
    claimed = store.claim(state)

    response = wizard.post(wizard.path + "/cancel")

    assert response.status_code == 409
    assert "This import is still processing. Try cancelling again in a moment." in response.text
    assert store.get(state.token, state.actor_id, state.session_id, state.dataset_id) == claimed
    store.finish(claimed)


def test_formats_route_holds_operation_until_cancel_can_consume(wizard, monkeypatch):
    wizard.upload()
    service = wizard.services.record_imports
    state = service.store._states[wizard.token]
    entered, release, cancel_started, cancel_done = Event(), Event(), Event(), Event()
    original = service.submitted_source_format_assistance

    def held_assistance(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=1)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "submitted_source_format_assistance", held_assistance)
    form = {
        "mapping_0": "code",
        "mapping_1": "amount",
        "source_0": "v1-text",
        "source_1": "v1-plain",
        "timezone_0": "",
        "timezone_1": "",
        "fold_0": "",
        "fold_1": "",
    }

    def cancel_during_formats():
        assert entered.wait(timeout=1)
        cancel_started.set()
        service.store.cancel(state.token, state.actor_id, state.session_id, state.dataset_id)
        cancel_done.set()

    def release_formats():
        assert entered.wait(timeout=1)
        assert cancel_started.wait(timeout=1)
        assert not cancel_done.wait(timeout=0.05)
        release.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        cancelling = executor.submit(cancel_during_formats)
        releasing = executor.submit(release_formats)
        assert wizard.post(wizard.path + "/formats", form).status_code == 200
        releasing.result(timeout=1)
        cancelling.result(timeout=1)

    with pytest.raises(NotFoundError):
        service.store.get(state.token, state.actor_id, state.session_id, state.dataset_id)


@pytest.mark.parametrize(
    "headers,expected",
    [
        ("mystery,amount,extra", ("code", "amount", None)),
        ("amount,mystery,extra", ("amount", "code", None)),
        ("renamed,amount,extra", (None, "amount", None)),
        ("mystery,amount", ("code", "amount")),
        ("mystery,amount,extra,code", ("code", "amount", None, "")),
    ],
)
def test_corrected_file_exact_column_compatibility_and_old_token_invalidation(
    wizard, headers, expected
):
    wizard.upload(b"mystery,amount,extra\nA,bad,ignore\n")
    wizard.preview(("code", "amount", None))
    old_path = wizard.path
    values = ",".join("1" if header == "amount" else "A" for header in headers.split(","))
    corrected = wizard.upload(f"{headers}\n{values}\n".encode(), "corrected.csv")
    assert corrected.status_code == 200
    assert wizard.get(old_path + "/step/4").status_code == 404
    state = next(iter(wizard.services.record_imports.store._states.values()))
    assert state.mapping == expected
    assert not state.mapping_ready and not state.validation_valid
    assert wizard.get(wizard.path + "/step/5").status_code == 404


def test_corrected_workbook_retains_only_matching_worksheet(wizard):
    wizard.upload(workbook(), "old.xlsx")
    wizard.post(wizard.path + "/worksheet", {"worksheet": "Second"})
    wizard.preview()
    page = wizard.upload(workbook(), "corrected.xlsx")
    assert states(page)[2] == ("3", "active")
    assert "Worksheet: Second" in page.text
    page = wizard.upload(workbook(("New", "Other")), "changed.xlsx")
    assert states(page)[1] == ("2", "active")
    assert wizard.get(wizard.path + "/mapping").status_code == 404


def test_rejected_replacement_keeps_active_import(wizard):
    wizard.upload()
    wizard.preview()
    old = wizard.path
    page = wizard.post(wizard.root, {"file": (io.BytesIO(b"bad"), "bad.json")})
    assert page.status_code == 400
    assert wizard.get(old + "/step/4").status_code == 200


def test_all_routes_reauthorize_and_reject_cross_session_dataset_and_csrf(wizard):
    wizard.upload()
    wizard.preview()
    for suffix in ("/step/1", "/step/2", "/step/3", "/step/4", "/step/5", "/mapping", "/preview"):
        other = wizard.client.application.test_client()
        assert other.get(wizard.path + suffix, base_url="https://localhost").status_code == 302
    for suffix in ("/confirm", "/cancel", "/worksheet", "/preview"):
        assert wizard.post(wizard.path + suffix, {"csrf_token": "wrong"}).status_code == 403
    another = create_owned_dataset(wizard.services.datasets, wizard.admin.id, "other", "Other", "")
    assert (
        wizard.get(wizard.path.replace("/imports/", f"/{another.key}/") + "/step/4").status_code
        == 404
    )
    # Another authenticated session of the same user cannot use the token.
    new = wizard.client.application.test_client()
    login = new.get("/login", base_url="https://localhost")
    csrf = login.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    new.post(
        "/login",
        base_url="https://localhost",
        data={"email": wizard.admin.email, "password": PASSWORD, "csrf_token": csrf},
    )
    assert new.get(wizard.path + "/step/4", base_url="https://localhost").status_code == 404
    assert states(new.get(wizard.root, base_url="https://localhost"))[0] == ("1", "active")


def test_technical_failure_is_safe_and_retryable(wizard, monkeypatch):
    wizard.upload()
    wizard.preview()
    wizard.get(wizard.path + "/step/4")
    importer = wizard.services.record_imports
    original = importer.uow

    def fail_write(*, write=False, serialized=False):
        if write:
            raise RuntimeError("SECRET INTERNAL DETAILS")
        return original(write=write, serialized=serialized)

    monkeypatch.setattr(importer, "uow", fail_write)
    failed = wizard.post(wizard.path + "/confirm")
    assert failed.status_code == 500
    assert "SECRET" not in failed.text
    assert "No records have been imported." in failed.text
    assert wizard.count() == 0
    monkeypatch.setattr(importer, "uow", original)
    assert "Added: 1" in wizard.post(wizard.path + "/confirm").text


def test_conflict_after_preview_requires_new_validation(wizard):
    wizard.upload()
    wizard.preview()
    wizard.get(wizard.path + "/step/4")
    wizard.services.dataset_records.create_record(
        wizard.admin.id, wizard.dataset.key, {"code": "A", "amount": "1"}
    )
    failed = wizard.post(wizard.path + "/confirm")
    assert failed.status_code == 409
    assert "No records have been imported" in failed.text
    assert wizard.post(wizard.path + "/confirm").status_code == 409
    assert wizard.count() == 1


def test_simultaneous_submit_claims_once_and_logout_does_not_resurrect(import_context, monkeypatch):
    _, services, admin, dataset = import_context
    importer = services.record_imports
    state, _, _ = importer.begin(admin.id, "one", dataset.key, "x.csv", b"code,amount\nA,1\n")
    importer.preview(admin.id, "one", dataset.key, state.token, ("code", "amount"))
    # Pause immediately after the atomic claim, before touching a database connection.
    entered, release = Event(), Event()
    original = importer.store.claim

    def pause(state):
        original(state)
        entered.set()
        assert release.wait(5)
        raise RuntimeError("paused")

    monkeypatch.setattr(importer.store, "claim", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pause, importer.store.get(state.token, admin.id, "one", dataset.id))
        assert entered.wait(5)
        with pytest.raises(NotFoundError):
            importer.confirm(admin.id, "one", dataset.key, state.token)
        claimed = importer.store.get(state.token, admin.id, "one", dataset.id)
        importer.revoke_session("one")
        importer.store.finish(claimed, imported_count=1)
        assert not importer.store._states
        release.set()
        with pytest.raises(RuntimeError):
            future.result()


def test_back_from_worksheet_preserves_new_selection(wizard):
    wizard.upload(workbook(), "file.xlsx")
    page = wizard.post(wizard.path + "/worksheet", {"worksheet": "Second", "go_step": "1"})
    assert states(page)[0] == ("1", "active")
    page = wizard.get(wizard.path + "/step/2")
    assert "value='Second' selected" in page.text
    assert wizard.get(wizard.path + "/step/4").status_code == 404


def test_unexpected_preview_failure_exposes_no_details(wizard, monkeypatch):
    wizard.upload()
    wizard.preview()

    def fail(*args, **kwargs):
        raise RuntimeError("INTERNAL SECRET")

    monkeypatch.setattr(wizard.services.record_imports, "_validate", fail)
    page = wizard.get(wizard.path + "/step/4")
    assert page.status_code == 500
    assert "No records have been imported" in page.text
    assert "INTERNAL" not in page.text
    assert wizard.count() == 0


def test_changed_mapping_invalidates_old_confirmation_even_when_validation_fails(
    wizard, monkeypatch
):
    optional_field(wizard, "active")
    wizard.upload(b"code,amount,active\nA,1,true\n")
    wizard.preview(("code", "amount", "active"))
    wizard.get(wizard.path + "/step/4")
    original = wizard.services.record_imports._validate

    def fail(*args, **kwargs):
        raise RuntimeError("INTERNAL SECRET")

    monkeypatch.setattr(wizard.services.record_imports, "_validate", fail)
    page = wizard.preview(("code", "amount", None))
    assert page.status_code == 500
    assert "value='__ignored__' selected" in page.text
    assert "INTERNAL" not in page.text
    assert wizard.post(wizard.path + "/confirm").status_code == 404
    assert wizard.get(wizard.path + "/step/5").status_code == 404
    monkeypatch.setattr(wizard.services.record_imports, "_validate", original)
    assert wizard.preview(("code", "amount", "active")).status_code == 200


def test_compact_mapping_discloses_full_escaped_examples_and_short_options(wizard):
    long_value = "<script>" + "x" * 100 + "</script>"
    page = wizard.upload(f"code,amount\n{long_value},1\nB,2\nC,3\nFOURTH,4\n".encode())
    assert "class='mapping-list' role='list'" in page.text
    assert re.findall(r"<fieldset[^>]*>", page.text) == [
        "<fieldset class='source-format-controls'>"
    ]
    assert "<legend>Source format for amount</legend>" in page.text
    assert "Full example values" in page.text
    assert "&lt;script&gt;" + "x" * 100 + "&lt;/script&gt;" in page.text
    assert "<script>" + "x" * 100 not in page.text
    assert "FOURTH" not in page.text
    assert "Target field for code" in page.text
    assert "Suggestion: Code" in page.text
    assert "High confidence in this automatic suggestion." in page.text
    assert "badge badge-info" not in page.text
    assert re.search(r"<summary[^>]*data-mapping-choice[^>]*>Mapped</summary>", page.text)
    assert "aria-describedby='mapping-rationale-0'" in page.text
    assert "role='tooltip'" in page.text
    assert re.search(r"<option value='code' selected[^>]*>Code</option>", page.text)
    assert "mapping-examples-0 mapping-choice-0" in page.text


def test_compact_mapping_errors_keep_control_associations(wizard):
    wizard.upload()
    page = wizard.preview(("code", "code"))
    assert "mapping-error-0" in page.text
    assert "mapping-error-1" in page.text
    assert sum(attrs.get("aria-invalid") == "true" for _, attrs in html_elements(page.text)) == 2
    assert html_tag_texts(page.text, "summary").count("Mapping error") == 2
    assert "1 field not mapped: Amount" in html_text(page.text)
    assert "This field is mapped more than once" in page.text


def test_preview_keeps_required_and_source_format_errors_together(wizard):
    wizard.services.dataset_fields.create_field(
        wizard.admin.id, wizard.dataset.key, "stamp", "Stamp", "timestamp"
    )
    wizard.upload(b"code,amount,stamp\nA,1,2024-10-27 02:30:00\n")
    page = wizard.preview(("code", "", "stamp"))
    text = html_text(page.text)
    assert "Map every required and identifier field." in text
    assert "Choose an unambiguous source format for this field." in text


def test_mapping_options_are_flat_ordered_and_reserved_selections_are_hidden(wizard):
    optional_field(wizard, "active")
    page = wizard.upload()
    code_select = re.search(r"<select id='mapping-0'.*?</select>", page.text, re.S)[0]
    assert "optgroup" not in code_select
    assert "value='amount'" in code_select
    assert "value='code' selected" in code_select
    assert " disabled" not in code_select
    assert "value='active'" in code_select
    assert code_select.index("— Choose a field —") < code_select.index("— Do not import —")


def test_do_not_import_follows_the_placeholder_in_the_compact_select(wizard):
    page = wizard.upload()
    code_select = re.search(r"<select id='mapping-0'.*?</select>", page.text, re.S)[0]
    assert "data-select='compact'" in code_select
    values = re.findall(r"<option value='([^']*)'", code_select)
    assert values[:2] == ["", "__ignored__"]
    assert ">— Do not import —</option>" in code_select


def test_ambiguous_mapping_hint_is_in_dropdown_and_badge_tooltip(wizard, import_context):
    _, services, admin, dataset = import_context
    for key, name in (("dutch_name", "Dutch Name"), ("english_name", "English Name")):
        services.dataset_fields.create_field(admin.id, dataset.key, key, name, "string")
    page = wizard.upload(b"name,code,amount\nExample,A,1\n")
    assert "selected>— Choose a field —</option>" in page.text
    assert "class='mapping-advice'" not in page.text
    tooltip = re.search(r"id='mapping-rationale-0'.*?</div>", page.text, re.S)[0]
    assert "Multiple possible fields — choose one" in tooltip
    assert html_text(page.text).count("Multiple possible fields — choose one") == 1


def test_mapping_instruction_and_readiness_are_visible_near_continue(wizard):
    def status(page):
        match = re.search(r"<p class='mapping-status'([^>]*)>(.*?)</p>", page.text, re.S)
        assert match
        return match.group(1), html_text(match.group(2)).strip()

    pending = wizard.upload()
    pending = wizard.preview(("", "amount"))
    attributes, message = status(pending)
    assert message == "1 column still needs a choice · 1 field not mapped: Code"
    assert "data-state='pending'" in attributes
    assert "alert-warning" not in attributes
    reason = re.search(r"<p class='mapping-reason help-text'([^>]*)>", pending.text)
    assert reason and "hidden" not in reason.group(1)
    assert "data-mapping-jump" in pending.text
    jump = re.search(r"<button\b(?=[^>]*data-mapping-jump)([^>]*)>", pending.text)
    assert jump
    jump_classes = re.search(r"class='([^']*)'", jump.group(1)).group(1).split()
    assert "mapping-jump" in jump_classes
    assert "button-link" not in jump_classes
    assert pending.text.index("Back") < pending.text.index("Cancel import")
    assert pending.text.index("mapping-status") < pending.text.index("Continue to preview")

    optional_field(wizard, "function")
    missing = wizard.upload()
    attributes, message = status(missing)
    assert message == (
        "Ready to continue · 2 fields mapped, 0 columns not imported · 1 field not in file"
    )
    assert "data-state='ready'" in attributes
    assert "hidden" in re.search(
        r"<p class='mapping-reason help-text'([^>]*)>", missing.text
    ).group(1)

    wizard.upload(b"code,amount,function,unknown\nA,1,fn,x\n")
    ready = wizard.preview(("code", "amount", "function", "__ignored__"))
    attributes, message = status(ready)
    assert message == "Ready to continue · 3 fields mapped, 1 column not imported"
    assert "data-state='ready'" in attributes
    assert "hidden" in re.search(r"<p class='mapping-reason help-text'([^>]*)>", ready.text).group(
        1
    )
    assert "data-mapping-jump" not in ready.text

    visible = html_text(ready.text)
    assert "Match each source column to a dataset field, or choose Do not import." in visible
    assert "File:" in visible and "records.csv" in visible
    assert "unmapped fields are omitted" not in visible
    assert "required fields mapped" not in visible
    assert "data-mapping-summary" not in ready.text
    assert "data-mapping-missing" not in ready.text
    assert "data-mapping-status" in ready.text
    assert ready.text.index("data-mapping-status") > ready.text.index("</form>")
    assert ready.text.index("data-mapping-status") < ready.text.index("data-mapping-continue")


def test_mapping_status_limits_field_names_and_cancel_is_left_of_forward_actions(wizard):
    for key in ("active", "subgroup", "region", "category"):
        wizard.services.dataset_fields.create_field(
            wizard.admin.id, wizard.dataset.key, key, key.title(), "string", True
        )
    page = wizard.upload(b"code,amount\nA,1\n")
    page = wizard.preview(("", ""))
    status_text = html_text(
        re.search(r"<span class='mapping-status-line'.*?</span>", page.text, re.S).group()
    )
    missing_count = int(re.search(r"(\d+) fields not mapped:", status_text).group(1))
    assert missing_count > 3
    field_names = re.findall(r"<code class='mapping-field-name'>(.*?)</code>", page.text)
    assert field_names == ["Code", "Amount", "Active"]
    assert f"and {missing_count - 3} more" in status_text
    assert page.text.count("data-mapping-jump") == 1
    assert page.text.index("class='import-actions-back'") < page.text.index(
        "class='import-actions-forward"
    )
    assert (
        page.text.count(
            "Map every required and identifier field. Optional fields may be left unmapped."
        )
        == 2
    )


@pytest.mark.parametrize("existing", [None, "matching", "deleted"])
def test_optional_fields_preview_explains_preserved_and_empty_values(wizard, existing):
    wizard.services.dataset_fields.create_field(
        wizard.admin.id, wizard.dataset.key, "function", "Function <script>", "string"
    )
    if existing:
        wizard.services.dataset_records.create_record(
            wizard.admin.id,
            wizard.dataset.key,
            {"code": "A" if existing == "matching" else "B", "amount": "1", "function": "Kept"},
        )
    wizard.upload()
    page = wizard.preview()
    assert page.status_code == 200
    explanation = (
        "Existing records keep their values; new records leave these fields empty."
        if existing == "matching"
        else "These fields stay empty."
    )
    assert f"Not in file: Function <script>. {explanation}" in html_text(page.text)
    if existing != "matching":
        assert "Existing records keep their values" not in page.text
    assert "<code>Function &lt;script&gt;</code>" in page.text
    assert "Import records" in page.text
    assert "every field must be mapped" not in page.text
    wizard.get(wizard.path + "/step/4")
    imported = wizard.post(wizard.path + "/confirm", {"acknowledge_deletions": "yes"})
    assert imported.status_code == 200
    assert wizard.count() == (2 if existing == "deleted" else 1)


@pytest.mark.parametrize("omitted", [0, 1, 2])
def test_ready_mapping_status_counts_fields_not_in_file(wizard, omitted):
    for key in ("active", "day")[:omitted]:
        optional_field(wizard, key)
    wizard.upload(b"code,amount,extra\nA,1,ignored\n")
    wizard.preview(("code", "amount", "__ignored__"))
    page = wizard.get(wizard.path + "/step/3")
    assert page.status_code == 200
    status = html_text(
        re.search(r"<span class='mapping-status-line'.*?</span>", page.text, re.S).group()
    ).strip()
    expected = "Ready to continue · 2 fields mapped, 1 column not imported"
    if omitted:
        expected += f" · {omitted} {'field' if omitted == 1 else 'fields'} not in file"
    assert status == expected
    assert "data-import-mode='new_version'" in page.text


def test_ready_mapping_target_update_does_not_count_fields_not_in_file(wizard):
    dataset = create_mapping_dataset(
        wizard.services, wizard.admin, "target-import", "Target import", records=("A",)
    )
    for key in ("target", "note"):
        wizard.services.dataset_fields.create_field(
            wizard.admin.id, dataset.key, key, key.title(), "string"
        )
    page = wizard.post(
        f"/datasets/{dataset.key}/records/import",
        {"file": (io.BytesIO(b"code,target\nA,updated\n"), "targets.csv")},
    )
    assert page.status_code == 200
    status = html_text(
        re.search(r"<span class='mapping-status-line'.*?</span>", page.text, re.S).group()
    ).strip()
    assert status == "Ready to continue · 2 fields mapped, 0 columns not imported"
    assert "data-import-mode='mapping_update'" in page.text
