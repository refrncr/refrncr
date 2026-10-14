import csv
import io
import json
import zipfile
from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace

from openpyxl import load_workbook

from refrncr.dataset_export import generate_export
from refrncr.datatypes import get_datatype
from refrncr.hierarchy_contracts import HierarchyNode
from refrncr.ports import RecordValue


def field(field_id, key, scope):
    return SimpleNamespace(
        id=field_id,
        key=key,
        name=key.title(),
        data_type="string",
        hierarchy_scope=scope,
        is_required=False,
        is_unique=False,
    )


def node(node_id, name, parent_id=None, values=()):
    return HierarchyNode(node_id, "dataset", name, parent_id, tuple(values))


def value(field_id, key, text):
    return RecordValue(field_id, key, "string", text, text.encode(), False)


def typed_value(field, raw):
    parsed, canonical_key = get_datatype(field.data_type).canonicalize(raw)
    return RecordValue(field.id, field.key, field.data_type, parsed, canonical_key, False)


def read_csv(artifact):
    return list(csv.reader(io.TextIOWrapper(artifact.content, encoding="utf-8-sig")))


def test_hierarchy_csv_data_contains_all_nodes_and_direct_parent():
    fields = (field("f1", "Name", "all_nodes"), field("f2", "code", "leaves"))
    nodes = (
        node("root", "Root", values=(value("f1", "Name", "top"),)),
        node("branch", "Branch", "root"),
        node("leaf", "Leaf", "branch", (value("f1", "Name", "context"), value("f2", "code", "L1"))),
    )

    artifact = generate_export("csv", fields, (), "hierarchy", hierarchy_nodes=nodes)
    rows = read_csv(artifact)

    assert rows[0] == ["Node ID", "Parent ID", "Name", "Parent", "Field: Name", "code"]
    assert rows[1][0:4] == ["root", "", "Root", ""]
    assert rows[2][0:4] == ["branch", "root", "Branch", "Root"]
    assert rows[2][5] == ""
    assert rows[3][4:] == ["context", "L1"]
    assert artifact.download_name == "hierarchy-data.csv"
    artifact.content.close()


def test_hierarchy_csv_bundle_contains_both_variants():
    fields = (field("f1", "code", "leaves"),)
    nodes = (node("root", "Root"), node("leaf", "Leaf", "root", (value("f1", "code", "L"),)))

    artifact = generate_export("csv_zip", fields, (), "hierarchy", hierarchy_nodes=nodes)

    with zipfile.ZipFile(artifact.content) as archive:
        assert archive.namelist() == ["hierarchy-data.csv", "hierarchy-flattened.csv"]
        assert archive.read("hierarchy-data.csv").startswith(b"\xef\xbb\xbf")
        assert archive.read("hierarchy-flattened.csv").startswith(b"\xef\xbb\xbf")
    assert artifact.media_type == "application/zip"
    assert artifact.extension == "zip"
    assert artifact.download_name == "hierarchy.zip"
    artifact.content.close()


def test_hierarchy_flattened_csv_aligns_levels_and_leaf_values():
    fields = (field("f1", "region", "all_nodes"),)
    nodes = (
        node("root", "Root"),
        node("branch", "Branch", "root", (value("f1", "region", "EU"),)),
        node("leaf", "Leaf", "branch", (value("f1", "region", "DE"),)),
    )

    artifact = generate_export(
        "csv", fields, (), "hierarchy", hierarchy_nodes=nodes, hierarchy_variant="flattened"
    )
    rows = read_csv(artifact)

    assert rows[0] == [
        "Node ID",
        "Parent ID",
        "Level 1",
        "Level 2",
        "Leaf",
        "region Level 1",
        "region Level 2",
        "region Leaf",
    ]
    assert rows[1] == ["leaf", "branch", "Root", "Branch", "Leaf", "", "EU", "DE"]
    artifact.content.close()


def test_hierarchy_xlsx_has_three_user_facing_sheets_and_json_has_metadata():
    fields = (field("f1", "code", "leaves"),)
    nodes = (node("root", "Root"), node("leaf", "Leaf", "root", (value("f1", "code", "L"),)))

    workbook_artifact = generate_export("xlsx", fields, (), "hierarchy", hierarchy_nodes=nodes)
    workbook = load_workbook(workbook_artifact.content, read_only=True, data_only=True)
    assert workbook.sheetnames == ["Data", "Flattened", "Hierarchy"]
    assert list(workbook["Data"].values)[0][:2] == ("Node ID", "Parent ID")
    assert list(workbook["Flattened"].values)[0][:2] == ("Node ID", "Parent ID")
    assert list(workbook["Hierarchy"].values)[2][0] == "Leaf"
    workbook.close()
    workbook_artifact.content.close()

    json_artifact = generate_export(
        "json",
        fields,
        (),
        "hierarchy",
        hierarchy_nodes=nodes,
        provenance={"type": "publication", "version": 2},
    )
    payload = json.load(json_artifact.content)
    assert payload["schema_version"] == 1
    assert payload["metadata"]["provenance"]["version"] == 2
    assert [node["node_id"] for node in payload["nodes"]] == ["root", "leaf"]
    assert payload["nodes"][0]["values"]["code"] is None
    json_artifact.content.close()


def test_missing_numeric_hierarchy_values_stay_null_and_typed():
    numeric = field("f1", "headcount", "all_nodes")
    numeric.data_type = "integer"
    nodes = (
        node("root", "Root"),
        node("leaf", "Leaf", "root", (RecordValue("f1", "headcount", "integer", 3, b"3", False),)),
    )

    csv_artifact = generate_export("csv", (numeric,), (), "hierarchy", hierarchy_nodes=nodes)
    rows = read_csv(csv_artifact)
    assert rows[1][-1] == ""
    assert rows[2][-1] == "3"
    csv_artifact.content.close()

    xlsx_artifact = generate_export("xlsx", (numeric,), (), "hierarchy", hierarchy_nodes=nodes)
    workbook = load_workbook(xlsx_artifact.content, read_only=True, data_only=True)
    values = list(workbook["Data"].values)
    assert values[1][-1] is None
    assert values[2][-1] == 3
    assert isinstance(values[2][-1], int)
    workbook.close()
    xlsx_artifact.content.close()

    json_artifact = generate_export("json", (numeric,), (), "hierarchy", hierarchy_nodes=nodes)
    payload = json.load(json_artifact.content)
    assert payload["nodes"][0]["values"]["headcount"] is None
    assert payload["nodes"][1]["values"]["headcount"] == 3
    json_artifact.content.close()


def test_reporting_csv_contains_every_node_in_tree_order_with_fill_down_and_safe_keys():
    region = field("f1", "region", "all_nodes")
    code = field("f2", "code", "leaves")
    reserved_name = field("f3", "name", "all_nodes")
    nodes = (
        node(
            "root",
            "=Executive",
            values=(value("f1", "region", "Global"), value("f3", "name", "Root value")),
        ),
        node(
            "commercial",
            "Commercial",
            "root",
            (
                value("f1", "region", "EU"),
                value("f2", "code", "inactive"),
                value("f3", "name", "Branch value"),
            ),
        ),
        node(
            "west",
            'West, "quoted"\nline',
            "commercial",
            (value("f1", "region", "NL"), value("f2", "code", "W")),
        ),
        node("operations", "Operations", "root", (value("f2", "code", "O"),)),
    )

    artifact = generate_export(
        "csv",
        (region, code, reserved_name),
        (),
        "organisation-concept",
        hierarchy_nodes=nodes,
        hierarchy_variant="reporting",
    )
    payload = artifact.content.read()
    rows = list(csv.reader(io.StringIO(payload[3:].decode("utf-8"), newline="")))

    assert payload.startswith(b"\xef\xbb\xbf")
    assert rows[0] == [
        "node_id",
        "name",
        "parent_id",
        "region",
        "code",
        "field_name",
        "depth",
        "is_leaf",
        "sort_order",
        "path",
        "level_1",
        "level_2",
        "level_3",
        "level_1_region",
        "level_1_name",
        "level_2_region",
        "level_2_name",
        "level_3_region",
        "level_3_name",
    ]
    assert [row[0] for row in rows[1:]] == ["root", "commercial", "west", "operations"]
    assert rows[1][1] == "'=Executive"
    assert rows[1][6:10] == ["1", "false", "1", "'=Executive"]
    assert rows[1][10:13] == ["'=Executive", "'=Executive", "'=Executive"]
    assert rows[2][4] == ""  # Leaves-only values stored on a branch remain inactive.
    assert rows[2][6:10] == ["2", "false", "2", "'=Executive/Commercial"]
    assert rows[2][10:13] == ["'=Executive", "Commercial", "Commercial"]
    assert rows[2][13::2] == ["Global", "EU", "EU"]
    assert rows[3][1] == 'West, "quoted"\nline'
    assert rows[3][6:9] == ["3", "true", "3"]
    assert rows[4][6:9] == ["2", "true", "4"]
    assert artifact.download_name == "organisation-concept-reporting.csv"
    artifact.content.close()


def test_reporting_excel_is_one_filtered_table_with_frozen_header_and_typed_values():
    amount = field("f1", "amount", "all_nodes")
    amount.data_type = "number"
    active = field("f2", "active", "all_nodes")
    active.data_type = "boolean"
    opened = field("f3", "opened", "leaves")
    opened.data_type = "date"
    nodes = (
        node("root", "+Root"),
        node(
            "leaf",
            "Leaf",
            "root",
            (
                RecordValue("f1", "amount", "number", Decimal("12.50"), b"12.5", False),
                RecordValue("f2", "active", "boolean", True, b"1", False),
                RecordValue("f3", "opened", "date", date(2026, 10, 14), b"2026-10-14", False),
            ),
        ),
    )

    artifact = generate_export(
        "xlsx",
        (amount, active, opened),
        (),
        "organisation-v2",
        hierarchy_nodes=nodes,
        hierarchy_variant="reporting",
    )
    workbook = load_workbook(artifact.content, data_only=False)
    worksheet = workbook["Reporting"]
    rows = list(worksheet.values)

    assert workbook.sheetnames == ["Reporting"]
    assert worksheet.freeze_panes == "A2"
    assert tuple(worksheet.tables) == ("ReportingTable",)
    assert worksheet.tables["ReportingTable"].ref.endswith("3")
    assert rows[1][1] == "+Root"
    assert worksheet["B2"].data_type == "s"
    amount_column = rows[0].index("amount")
    active_column = rows[0].index("active")
    opened_column = rows[0].index("opened")
    assert rows[2][amount_column] == 12.5
    assert rows[2][active_column] is True
    assert rows[2][opened_column] == "2026-10-14"
    assert artifact.download_name == "organisation-v2-reporting.xlsx"
    workbook.close()
    artifact.content.close()


def test_reporting_reserved_field_names_are_stable_independent_of_display_order():
    reserved = field("f1", "name", "all_nodes")
    prefixed = field("f2", "field_name", "all_nodes")
    nodes = (node("root", "Root"),)

    first = generate_export(
        "csv",
        (reserved, prefixed),
        (),
        "first",
        hierarchy_nodes=nodes,
        hierarchy_variant="reporting",
    )
    second = generate_export(
        "csv",
        (prefixed, reserved),
        (),
        "second",
        hierarchy_nodes=nodes,
        hierarchy_variant="reporting",
    )
    first_headers = read_csv(first)[0]
    second_headers = read_csv(second)[0]

    assert "field_field_name" in first_headers and "field_name" in first_headers
    assert "field_field_name" in second_headers and "field_name" in second_headers
    first.content.close()
    second.content.close()


def test_reporting_exports_all_supported_datatypes_through_existing_serializers():
    definitions = (
        ("text", "string", "=safe text"),
        ("decimal", "number", "12.50"),
        ("enabled", "boolean", "true"),
        ("day", "date", "2026-10-14"),
        ("count", "integer", "7"),
        ("ratio", "float", "1.25"),
        ("clock", "time", "09:30:00"),
        ("local", "datetime", "2026-10-14T09:30:00"),
        ("instant", "timestamp", "2026-10-14T09:30:00+02:00"),
    )
    fields = []
    stored_values = []
    for index, (key, data_type, raw) in enumerate(definitions, start=1):
        item = field(f"f{index}", key, "all_nodes")
        item.data_type = data_type
        fields.append(item)
        stored_values.append(typed_value(item, raw))
    nodes = (node("root", "Root", values=tuple(stored_values)),)

    csv_artifact = generate_export(
        "csv",
        fields,
        (),
        "types",
        hierarchy_nodes=nodes,
        hierarchy_variant="reporting",
    )
    csv_row = dict(zip(*read_csv(csv_artifact), strict=True))
    assert csv_row == {
        "node_id": "root",
        "name": "Root",
        "parent_id": "",
        "text": "'=safe text",
        "decimal": "12.50",
        "enabled": "true",
        "day": "2026-10-14",
        "count": "7",
        "ratio": "1.25",
        "clock": "09:30:00",
        "local": "2026-10-14T09:30:00",
        "instant": "2026-10-14T07:30:00Z",
        "depth": "1",
        "is_leaf": "true",
        "sort_order": "1",
        "path": "Root",
        "level_1": "Root",
        "level_1_text": "'=safe text",
        "level_1_decimal": "12.50",
        "level_1_enabled": "true",
        "level_1_day": "2026-10-14",
        "level_1_count": "7",
        "level_1_ratio": "1.25",
        "level_1_clock": "09:30:00",
        "level_1_local": "2026-10-14T09:30:00",
        "level_1_instant": "2026-10-14T07:30:00Z",
    }
    csv_artifact.content.close()

    xlsx_artifact = generate_export(
        "xlsx",
        fields,
        (),
        "types",
        hierarchy_nodes=nodes,
        hierarchy_variant="reporting",
    )
    workbook = load_workbook(xlsx_artifact.content, data_only=False)
    headers, exported = list(workbook["Reporting"].values)
    values_by_key = dict(zip(headers, exported, strict=True))
    assert values_by_key["text"] == "=safe text"
    assert values_by_key["decimal"] == 12.5
    assert values_by_key["enabled"] is True
    assert values_by_key["day"] == date(2026, 10, 14).isoformat()
    assert values_by_key["count"] == 7
    assert values_by_key["ratio"] == 1.25
    assert values_by_key["clock"] == time(9, 30).isoformat()
    assert values_by_key["local"] == datetime(2026, 10, 14, 9, 30).isoformat()
    assert values_by_key["instant"] == datetime(
        2026, 10, 14, 7, 30, tzinfo=UTC
    ).isoformat().replace("+00:00", "Z")
    workbook.close()
    xlsx_artifact.content.close()
