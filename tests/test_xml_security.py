import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import Mock

import openpyxl.xml
import pytest
from defusedxml.common import DefusedXmlException
from openpyxl import Workbook
from support import _dataset_browser, create_dataset, create_field, csrf_token, login, request

from refrncr import cli
from refrncr.app import create_application
from refrncr.record_import import UNREADABLE_XLSX_MESSAGE, ImportValidationError, parse_source
from refrncr.web import create_flask_app


def workbook_bytes():
    workbook = Workbook()
    workbook.active.append(["code"])
    workbook.active.append(["safe"])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def test_start_rejects_disabled_xml_protection_before_database_changes(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(openpyxl.xml, "DEFUSEDXML", False)
    prepare = Mock()
    build = Mock()
    serve = Mock()
    monkeypatch.setattr(cli, "prepare_database", prepare)
    monkeypatch.setattr(cli, "build_application", build)
    monkeypatch.setattr(cli.uvicorn, "run", serve)
    result = cli.main(["start", "--database-url", f"sqlite:///{tmp_path / 'unused.db'}"])
    assert result == 1
    assert "XML protection" in capsys.readouterr().err
    prepare.assert_not_called()
    build.assert_not_called()
    serve.assert_not_called()


@pytest.mark.parametrize("factory", ["flask", "application"])
def test_direct_application_creation_requires_xml_protection(monkeypatch, factory):
    monkeypatch.setattr(openpyxl.xml, "DEFUSEDXML", False)
    users = Mock()
    with pytest.raises(RuntimeError, match="XML protection"):
        if factory == "flask":
            create_flask_app(users, None, None, None, None)
        else:
            create_application(
                users,
                None,
                None,
                None,
                None,
                authorization=None,
                datasets=None,
                dataset_fields=None,
                dataset_business_keys=None,
            )
    users.bootstrap_organisation.assert_not_called()


def test_direct_excel_import_requires_xml_protection_but_csv_still_works(monkeypatch):
    content = workbook_bytes()
    monkeypatch.setattr(openpyxl.xml, "DEFUSEDXML", False)
    with pytest.raises(RuntimeError, match="XML protection"):
        parse_source("safe.xlsx", content, "Sheet")
    assert parse_source("safe.csv", b"code,label\nsafe,Safe\n").rows[0].values == ("safe", "Safe")


def replace_worksheet(xml, part="xl/worksheets/sheet1.xml"):
    output = io.BytesIO()
    # Stored entries ensure these tiny XML probes cannot hit the compression-ratio guard.
    with zipfile.ZipFile(io.BytesIO(workbook_bytes())) as source:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
            for name in source.namelist():
                target.writestr(name, xml if name == part else source.read(name))
    return output.getvalue()


@pytest.mark.timeout(5)
@pytest.mark.parametrize(
    "entity",
    [
        '<!ENTITY a "small"><!ENTITY b "&a;&a;&a;&a;">',
        '<!ENTITY b SYSTEM "file:///must-not-be-read-ref48">',
    ],
)
def test_xml_entities_are_rejected_by_safe_parser_not_zip_limits(entity):
    xml = (
        '<?xml version="1.0"?><!DOCTYPE worksheet [' + entity + "]>"
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:A2"/><sheetData><row r="1"><c r="A1" t="inlineStr">'
        "<is><t>&b;</t></is></c></row></sheetData></worksheet>"
    ).encode()
    with pytest.raises(ImportValidationError) as error:
        parse_source("entities.xlsx", replace_worksheet(xml))
    assert str(error.value) == UNREADABLE_XLSX_MESSAGE
    cause = error.value
    while cause.__cause__ is not None:
        cause = cause.__cause__
    assert isinstance(cause, DefusedXmlException)


@pytest.mark.timeout(5)
def test_late_worksheet_xml_error_is_clean_after_successful_workbook_open():
    xml = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="A1:A2"/><!--' + "padding " * 5000 + "--><sheetData>"
        '<row r="1"><c r="A1" t="inlineStr"><is><t>code</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>&undefined;</t></is></c></row>'
        "</sheetData></worksheet>"
    ).encode()
    content = replace_worksheet(xml)
    assert parse_source("late.xlsx", content).worksheets == ("Sheet",)
    with pytest.raises(ImportValidationError, match="modern Excel") as error:
        parse_source("late.xlsx", content, "Sheet")
    assert str(error.value) == UNREADABLE_XLSX_MESSAGE


@pytest.mark.timeout(5)
@pytest.mark.parametrize("part", ["xl/workbook.xml", "[Content_Types].xml"])
def test_workbook_metadata_xml_errors_are_clean_with_either_parser_backend(part):
    with pytest.raises(ImportValidationError) as error:
        parse_source("broken-xml.xlsx", replace_worksheet(b"<broken>", part))
    assert str(error.value) == UNREADABLE_XLSX_MESSAGE


@pytest.mark.timeout(5)
def test_workbook_entity_amplification_is_rejected_cleanly_with_either_backend():
    with zipfile.ZipFile(io.BytesIO(workbook_bytes())) as source:
        xml = source.read("xl/workbook.xml")
    declarations = ['<!ENTITY a "1234567890">']
    for previous, name in zip("abcde", "bcdef", strict=True):
        declarations.append(f'<!ENTITY {name} "' + f"&{previous};" * 10 + '">')
    xml = ("<!DOCTYPE workbook [" + "".join(declarations) + "]>").encode() + xml.replace(
        b"</workbook>", b"&f;</workbook>"
    )
    with pytest.raises(ImportValidationError) as error:
        parse_source("amplification.xlsx", replace_worksheet(xml, "xl/workbook.xml"))
    assert str(error.value) == UNREADABLE_XLSX_MESSAGE


def test_valid_workbook_still_imports_with_xml_protection():
    assert openpyxl.xml.DEFUSEDXML is True
    assert parse_source("safe.xlsx", workbook_bytes(), "Sheet").rows[0].values == ("safe",)


def test_entity_workbook_returns_safe_browser_validation_error_without_writes():
    _, services, admin, client = _dataset_browser()
    dataset = create_dataset(services, admin, "xml-check")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    login(client)
    url = f"/datasets/{dataset.key}/records/import"
    form = request(client, "get", url)
    xml = (
        b'<!DOCTYPE worksheet [<!ENTITY private_payload "not-for-display">]>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>'
    )
    response = request(
        client,
        "post",
        url,
        data={
            "csrf_token": csrf_token(form),
            "file": (io.BytesIO(replace_worksheet(xml)), "entities.xlsx"),
        },
    )
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "save a new .xlsx copy" in body
    assert "private_payload" not in body and "not-for-display" not in body
    assert not services.dataset_records.list_records(admin.id, dataset.key).records


@pytest.mark.parametrize("disabled", ["False", "false", "0", ""])
def test_late_environment_override_also_blocks_import(monkeypatch, disabled):
    monkeypatch.setenv("OPENPYXL_DEFUSEDXML", disabled)
    with pytest.raises(RuntimeError, match="XML protection"):
        parse_source("safe.xlsx", workbook_bytes(), "Sheet")


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.slow
def test_fresh_process_rejects_unsafe_start_but_keeps_help_and_admin(tmp_path, missing):
    # Block discovery before importing openpyxl to simulate an incomplete installation.
    program = """
import importlib.abc
import sys
class MissingDefusedXml(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "defusedxml" or fullname.startswith("defusedxml."):
            raise ModuleNotFoundError("defusedxml unavailable in test")
if sys.argv[1] == "missing":
    sys.meta_path.insert(0, MissingDefusedXml())
from refrncr import cli
import openpyxl.xml
assert openpyxl.xml.DEFUSEDXML is False
assert cli.main(["start", "--database-url", sys.argv[2]]) == 1
for args in (["--help"], ["user", "create-admin", "--help"]):
    try:
        cli.main(args)
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("help did not exit")
assert cli.main(["user", "create-admin", "--email", "admin@example.com",
                 "--display-name", "Administrator", "--database-url", sys.argv[3]],
                password_reader=lambda _: "correct horse battery staple") == 0
"""
    env = {**os.environ, "OPENPYXL_DEFUSEDXML": "True" if missing else "False"}
    database = tmp_path / "must-not-exist.db"
    admin_database = tmp_path / "admin.db"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            "missing" if missing else "disabled",
            f"sqlite:///{database}",
            f"sqlite:///{admin_database}",
        ],
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "XML protection" in result.stderr
    assert "Traceback" not in result.stderr
    assert not database.exists()
    assert admin_database.exists()
