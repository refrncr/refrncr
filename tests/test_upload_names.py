import pytest

from refrncr import hierarchy_import, record_import


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("/tmp/uploads/source.csv", "source.csv"),
        (r"C:\\tmp\\uploads\\source.csv", "source.csv"),
        ("  source.csv  ", "source.csv"),
        ("   ", "import"),
        ("x" * 260, "x" * 255),
        (None, "import"),
    ],
)
def test_import_flows_share_upload_filename_normalization(name, expected):
    assert record_import.safe_upload_filename is hierarchy_import.safe_upload_filename
    assert record_import.safe_upload_filename(name) == expected
    assert hierarchy_import.safe_upload_filename(name) == expected
