"""Guards for the incremental mypy baseline."""

import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "refrncr"
MAX_TYPE_IGNORES = 42
TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore")
CODED_TYPE_IGNORE = re.compile(
    r"#\s*type:\s*ignore\["
    r"[a-z][a-z0-9-]*(?:,\s*[a-z][a-z0-9-]*)*"
    r"\](?:\s|$)"
)


def test_type_ignores_are_specific_and_do_not_exceed_baseline():
    ignores = []
    invalid = []
    for path in SOURCE_ROOT.rglob("*.py"):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if TYPE_IGNORE.search(line) is None:
                continue
            location = f"{path.relative_to(SOURCE_ROOT.parent.parent)}:{line_number}"
            ignores.append(location)
            if CODED_TYPE_IGNORE.search(line) is None:
                invalid.append(location)

    assert invalid == [], "Type ignores must name specific error codes:\n" + "\n".join(invalid)
    assert len(ignores) <= MAX_TYPE_IGNORES, (
        f"Type-ignore baseline increased: {len(ignores)} > {MAX_TYPE_IGNORES}. "
        "Fix the new type error instead; lower MAX_TYPE_IGNORES when existing ignores are removed."
    )
