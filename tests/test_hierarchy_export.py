import csv
import io
import json
from types import SimpleNamespace

from openpyxl import load_workbook

from refrncr.dataset_export import generate_export
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
    assert all(
        cell not in {"Node ID", "Parent ID"}
        for sheet in workbook.worksheets
        for row in sheet.values
        for cell in row
    )
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
