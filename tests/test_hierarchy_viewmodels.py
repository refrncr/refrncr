from decimal import Decimal
from types import SimpleNamespace

from refrncr.web.viewmodels.hierarchies import hierarchy_presentation, sort_hierarchy_rows


def node(identifier, name, parent_id=None, *, values=()):
    return SimpleNamespace(id=identifier, name=name, parent_id=parent_id, values=values)


def test_tree_builds_depth_paths_values_and_sorted_siblings():
    field = SimpleNamespace(id="field", key="code", data_type="string")
    value = SimpleNamespace(field_id="field", field_key="code", data_type="string", value="B")
    root = node("root", "Root")
    second = node("second", "Zulu", "root")
    first = node("first", "Alpha", "root", values=(value,))
    view = SimpleNamespace(hierarchy_nodes=(second, root, first), fields=(field,))

    rows, children = hierarchy_presentation(view)

    assert [row["node"].id for row in rows] == ["root", "first", "second"]
    assert rows[1]["depth"] == 1
    assert rows[1]["ancestors"] == ("Root",)
    assert rows[1]["path"] == "Root / Alpha"
    assert rows[1]["values"] == {"code": "B"}
    assert rows[1]["leaf"] is True
    assert [item.id for item in children["root"]] == ["first", "second"]


def test_disconnected_nodes_remain_visible():
    orphan = node("orphan", "Orphan", "missing")

    rows, _children = hierarchy_presentation(SimpleNamespace(hierarchy_nodes=(orphan,), fields=()))

    assert [row["node"].id for row in rows] == ["orphan"]
    assert rows[0]["parent_name"] is None


def row(identifier, name, parent, path, value=None):
    return {
        "node": node(identifier, name),
        "parent_name": parent,
        "path": path,
        "values": {"code": value} if value is not None else {},
    }


def test_sorting_by_name_and_parent_is_case_insensitive_with_path_tiebreaker():
    rows = [row("b", "beta", "Zulu", "Z / beta"), row("a", "Alpha", "alpha", "A / Alpha")]

    assert [item["node"].id for item in sort_hierarchy_rows(rows, "name", None)] == ["a", "b"]
    assert [item["node"].id for item in sort_hierarchy_rows(rows, "parent", None)] == ["a", "b"]


def test_numeric_keys_sort_as_numbers_and_missing_values_first():
    field = SimpleNamespace(key="code", data_type="number")
    rows = [
        row("ten", "Ten", None, "Ten", Decimal("10")),
        row("missing", "Missing", None, "Missing"),
        row("two", "Two", None, "Two", Decimal("2")),
    ]

    assert [item["node"].id for item in sort_hierarchy_rows(rows, "key", field)] == [
        "missing",
        "two",
        "ten",
    ]
    assert [
        item["node"].id for item in sort_hierarchy_rows(rows, "key", field, descending=True)
    ] == ["ten", "two", "missing"]


def test_text_keys_sort_case_insensitively():
    field = SimpleNamespace(key="code", data_type="string")
    rows = [row("z", "Z", None, "Z", "zulu"), row("a", "A", None, "A", "Alpha")]

    assert [item["node"].id for item in sort_hierarchy_rows(rows, "key", field)] == ["a", "z"]
