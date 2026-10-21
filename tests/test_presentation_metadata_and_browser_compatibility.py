"""REF-165 final-review regressions; browser behaviour has a dedicated Chrome runner."""

import hashlib
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from support import (
    body,
    create_dataset,
    login,
    request,
    session_csrf,
)

from refrncr.models import Dataset
from refrncr.presentation_metadata import (
    ICON_SPRITES,
    LEGACY_ICON_SPRITE,
    dataset_icon,
    dataset_icon_sprite,
    rendered_dataset_icon,
)

ROOT = Path(__file__).parents[1]


def test_retired_lucide_symbols_keep_their_original_geometry(dataset_browser):
    _, _, _, client = dataset_browser
    sprite = request(client, "get", f"/static/{LEGACY_ICON_SPRITE}")
    # Frozen subset of the previously shipped 0.468.0 sprite, not newly invented glyphs.
    assert hashlib.sha256(sprite.data).hexdigest() == (
        "54279ae9585b7deb66c0813dcfb1d1735405a736d3f36b5d75a10b741389f554"
    )
    symbols = ET.fromstring(sprite.data).findall(".//{http://www.w3.org/2000/svg}symbol")
    assert len(symbols) == 74
    assert all(ICON_SPRITES[s.attrib["id"]] == LEGACY_ICON_SPRITE for s in symbols)
    assert all(re.fullmatch(r"[a-z0-9-]{1,64}", s.attrib["id"]) for s in symbols)
    for unsafe in ("../history", "history#x", "https://example.com", "<svg>", "x" * 65):
        assert dataset_icon(unsafe) is None
        assert rendered_dataset_icon(unsafe) == "table"
        assert dataset_icon_sprite(unsafe) == "images/lucide-1.46.0.svg"


def test_all_retired_icons_survive_ordinary_metadata_edits(context):
    c = context
    for name, sprite in ICON_SPRITES.items():
        if sprite != LEGACY_ICON_SPRITE:
            continue
        # Simulate a value already stored before the upgrade.
        with c.sessions.begin() as session:
            session.get(Dataset, c.d.id).icon = name
        saved = c.s.datasets.update_dataset(c.a.id, c.d.key, "Renamed", "", icon=name)
        assert saved.icon == name
        assert c.s.datasets.get_dataset_for_edit(c.a.id, c.d.key).icon == name
        assert rendered_dataset_icon(name) == name
        assert dataset_icon_sprite(name) == LEGACY_ICON_SPRITE


def test_history_icon_survives_the_browser_metadata_form(dataset_browser):
    factory, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "legacy-icon", "Legacy icon")
    with factory.begin() as session:
        session.get(Dataset, dataset.id).icon = "history"
    login(client)
    page = body(request(client, "get", "/datasets/legacy-icon/concept?edit=all"))
    assert "lucide-legacy-0.468.0.svg#history" in page
    assert "name='icon' value='history'" in page
    response = request(
        client,
        "post",
        "/datasets/legacy-icon",
        data={"csrf_token": session_csrf(client, services), "name": "Renamed", "icon": "history"},
    )
    assert response.status_code == 302
    page = body(request(client, "get", response.location))
    assert "lucide-legacy-0.468.0.svg#history" in page
    assert services.datasets.get_dataset_for_edit(admin.id, dataset.key).icon == "history"


def test_export_forms_have_a_safe_get_fallback(dataset_browser):
    _, services, admin, client = dataset_browser
    dataset = create_dataset(services, admin, "fallback", "Fallback")
    services.datasets.grant_export_access(admin.id, dataset.key, "user", admin.id)
    login(client)
    page = body(request(client, "get", "/datasets/fallback/concept"))
    targets = re.findall(r"data-inline-refresh='([^']+)'", page)
    assert targets == [
        "/datasets/fallback?tab=access-management#access-management" for _ in range(2)
    ]
    assert request(client, "get", targets[0]).status_code == 200
    legacy = request(client, "get", "/datasets/fallback/configure")
    assert legacy.status_code == 302
    assert legacy.location.endswith(
        "/datasets/fallback/concept?tab=dataset-details#dataset-details"
    )
    assert request(client, "get", "/datasets/fallback/export-access").status_code == 405


def test_section_tabs_javascript_failure_and_recovery():
    result = subprocess.run(
        ["node", str(ROOT / "tests/javascript/ref165_tabs.cjs")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
