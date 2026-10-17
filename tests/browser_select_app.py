"""Disposable in-memory REF-246 browser fixture for the Select component; never use real data."""

from flask import jsonify, request, url_for
from support import dataset_browser

from refrncr.web import authenticated_route, create_flask_app

factory, s, admin, _ = dataset_browser.__wrapped__()
for i in range(3):
    s.teams.create_team(admin.id, f"Domain {i}")
app = create_flask_app(
    s.users,
    s.authentication,
    s.sessions,
    s.teams,
    s.memberships,
    audit_history=s.audit_history,
    allow_loopback_http=True,
)
app.jinja_env.auto_reload = True

COUNTRIES = [
    ("AT", "Austria"),
    ("BE", "Belgium"),
    ("DE", "Germany"),
    ("DK", "Denmark"),
    ("ES", "Spain"),
    ("FR", "France"),
    ("IE", "Ireland"),
    ("IT", "Italy"),
    ("LU", "Luxembourg"),
    ("NL", "Netherlands"),
    ("NZ", "New Zealand"),
    ("PG", "Papua New Guinea"),
    ("PT", "Portugal"),
    ("SE", "Sweden"),
]
FIELDS = [
    "code",
    "name",
    "description",
    "country_code",
    "valid_from",
    "valid_to",
    "owner",
    "region",
    "currency",
    "segment",
    "manager",
    "parent_code",
]
PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Select fixture</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='css/refrncr.css') }}">
  <script src="{{ url_for('static', filename='js/select.js') }}" defer></script>
</head>
<body>
<main id="content" style="max-width: 40rem; padding: 24px;">
  <h1>Select fixture</h1>
  <form id="fixture-form" action="{{ url_for('select_fixture_echo') }}" method="get">
    {{ select_field('Country', 'country', 'country', countries, placeholder='Choose a country',
                    required=true, noun='countries') }}
    {{ select_field('Status', 'status', 'status', statuses, selected='published') }}
    {{ select_field('Target field', 'target', 'target', fields, compact=true, noun='fields') }}
    <button class="button button-primary" type="submit">Send</button>
  </form>
  <div id="fixture-panel"
       style="height: 320px; margin-top: 24px; overflow-y: auto; border: 1px solid;">
    <div style="height: 240px;"></div>
    {{ select_field('Owner team', 'team', 'team', teams) }}
  </div>
  <label>Data type <select name="data_type" data-select>
    <option value="string">Text</option><option value="integer">Whole number</option>
  </select></label>
  <button class="button button-secondary" type="button" id="add-option">Add option</button>
  <button class="button button-secondary" type="button" id="set-value">Set value</button>
  <button class="button button-secondary" type="button" id="focus-native">Focus native</button>
  <p id="changes">0</p>
</main>
<script>
  document.getElementById("add-option").addEventListener("click", () => {
    const option = document.createElement("option");
    option.value = "late";
    option.textContent = "Late team";
    document.getElementById("team").append(option);
  });
  document.getElementById("set-value").addEventListener("click", () => {
    document.getElementById("country").value = "DE";
  });
  document.getElementById("focus-native").addEventListener("click", () => {
    document.getElementById("status").focus();
  });
  document.getElementById("status").addEventListener("change", () => {
    const counter = document.getElementById("changes");
    counter.textContent = String(Number(counter.textContent) + 1);
  });
</script>
</body>
</html>"""


@app.get("/select-fixture")
@authenticated_route
def select_fixture():
    template = app.jinja_env.from_string("{% from 'components.html' import select_field %}" + PAGE)
    return template.render(
        url_for=url_for,
        countries=[{"value": code, "label": name, "code": code} for code, name in COUNTRIES],
        statuses=[
            {"value": "draft", "label": "Draft", "badge": "draft"},
            {"value": "published", "label": "Published", "badge": "published"},
            {"value": "removed", "label": "Removed", "badge": "removed", "disabled": True},
        ],
        fields=[(field, field) for field in FIELDS],
        teams=[(f"team-{i}", f"Team {i}") for i in range(6)],
    )


@app.get("/select-fixture/echo")
@authenticated_route
def select_fixture_echo():
    return jsonify(request.args.to_dict())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5246, threaded=True, use_reloader=False)
