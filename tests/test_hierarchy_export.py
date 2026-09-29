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
    assert rows[2][5] == "Not applicable"
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
    assert rows[1] == ["leaf", "branch", "Root", "Branch", "Leaf", "Empty", "EU", "DE"]
    artifact.content.close()


def test_hierarchy_xlsx_has_three_user_facing_sheets_and_json_has_metadata():
    fields = (field("f1", "code", "leaves"),)
    nodes = (node("root", "Root"), node("leaf", "Leaf", "root", (value("f1", "code", "L"),)))

    workbook_artifact = generate_export("xlsx", fields, (), "hierarchy", hierarchy_nodes=nodes)
    workbook = load_workbook(workbook_artifact.content, read_only=True, data_only=True)
    assert workbook.sheetnames == ["Data", "Flattened", "Hierarchy"]
    assert list(workbook["Hierarchy"].values)[2][2] == "Leaf"
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
    json_artifact.content.close()
