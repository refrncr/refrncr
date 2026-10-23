"""Overview attention, privacy and navigation behavior on SQLite and PostgreSQL."""

from datetime import timedelta

import pytest
from sqlalchemy import event, select
from support import NOW, MutableClock, audits, body, login, request
from test_mapping_source_updates import fixture as mapping_fixture
from test_mapping_source_updates import next_source, rows, update
from test_mapping_source_versions import prepare, publish_table

from refrncr.models import Dataset, DatasetConcept, DatasetVisit
from refrncr.overview import OverviewService
from refrncr.services import AuthorizationError


def service(c):
    c.s.datasets.uow.clock = c.s.dataset_versions.clock = MutableClock(NOW)
    with c.sessions.begin() as session:
        session.get(Dataset, c.d.id).created_at = NOW - timedelta(days=1)
    return OverviewService(c.s.datasets.uow, c.s.authorization.datasets)


def attention(view, dataset_id):
    return next(card for card in view.attention if card.dataset.id == dataset_id)


def reason(card, number):
    return next(r for r in card.reasons if r.number == number)


def age_draft(c, dataset_id, days):
    with c.sessions.begin() as session:
        session.get(DatasetConcept, dataset_id).updated_at = NOW - timedelta(days=days)


def test_team_ownership_admin_override_and_explicit_grants(database_context):
    c = database_context
    overview = service(c)
    age_draft(c, c.d.id, 14)
    assert not overview.read(c.a.id).attention
    assert not overview.read(c.a.id).has_team
    assert reason(attention(overview.read(c.a.id, all_owners=True), c.d.id), 6)
    c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", c.u.id)
    assert not overview.read(c.u.id).attention
    with pytest.raises(AuthorizationError):
        overview.read(c.u.id, all_owners=True)
    membership = c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    assert reason(attention(overview.read(c.u.id), c.d.id), 6)
    c.s.memberships.delete_membership(c.a.id, c.owner.id, membership.id)
    assert not overview.read(c.u.id).attention
    c.s.users.disable_user(c.a.id, c.u.id)
    with pytest.raises(AuthorizationError):
        overview.read(c.u.id)


@pytest.mark.parametrize("days,expected", [(13.999, False), (14, True), (21, True)])
def test_draft_age_boundary(database_context, days, expected):
    c = database_context
    overview = service(c)
    age_draft(c, c.d.id, days)
    assert bool(overview.read(c.a.id, all_owners=True).attention) is expected


@pytest.mark.parametrize("days,expected", [(1, True), (7, True), (8, False)])
def test_scheduled_horizon_and_cancelled_versions(database_context, days, expected):
    c = prepare(database_context)
    overview = service(c)
    version = publish_table(c, c.d, NOW.date() + timedelta(days=days))
    view = overview.read(c.a.id, all_owners=True)
    assert bool(view.attention) is expected
    if expected:
        assert reason(attention(view, c.d.id), 5).version_id == version.id
    c.s.dataset_versions.cancel(c.a.id, c.d.id, version.id)
    assert not overview.read(c.a.id, all_owners=True).attention


def test_mapping_current_scheduled_and_draft_destinations(database_context):
    c = database_context
    mapping, first, published = mapping_fixture(c)
    overview = service(c)
    second = next_source(c, day=30)
    card = attention(overview.read(c.a.id, all_owners=True), mapping.id)
    assert card.group == "Coming up"
    assert reason(card, 4).version_id == published.id
    assert not any(r.number == 1 for r in card.reasons)
    # Source announcements have no seven-day horizon.
    c.s.datasets.uow.clock.advance(days=30)
    card = attention(overview.read(c.a.id, all_owners=True), mapping.id)
    assert card.group == "Needs action"
    assert reason(card, 1).severity == "warning"
    update(c, mapping)
    card = attention(overview.read(c.a.id, all_owners=True), mapping.id)
    assert reason(card, 1).draft
    assert f"Draft already uses V{second.number}" in reason(card, 1).text
    assert reason(card, 7).draft
    assert "1 unmapped" in reason(card, 7).text
    assert "1 removed from source" in reason(card, 7).text


def test_blocked_source_is_primary_and_counts_are_independent(database_context):
    c = database_context
    mapping, _, _ = mapping_fixture(c)
    overview = service(c)
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    for row in c.s.dataset_records.list_records(c.a.id, c.d.key).records:
        c.s.dataset_records.delete_record(c.a.id, c.d.key, row.id, row.revision)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Label", "string", True, True)
    c.s.dataset_business_keys.set_business_key(c.a.id, c.d.key, ("label",))
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "code", "Code", "string", False, False)
    publish_table(c, c.d, NOW.date() + timedelta(days=1))
    c.s.dataset_versions.create_concept(c.a.id, mapping.id)
    age_draft(c, mapping.id, 20)
    view = overview.read(c.a.id, all_owners=True)
    card = attention(view, mapping.id)
    assert card.primary.number == 2
    assert card.group == "Needs action"
    assert {r.number for r in card.reasons} >= {2, 4, 6}
    assert "Ask the owner" in card.primary.text
    assert dict((n, count) for n, _, count in view.tallies)[6] == 1
    assert sum(item.dataset.id == mapping.id for item in view.attention) == 1


def test_unmapped_current_and_scheduled_remain_until_both_are_clean(database_context):
    c = database_context
    mapping, _, _ = mapping_fixture(c, publish=False)
    overview = service(c)
    row = rows(c, mapping)["a"]
    c.s.dataset_records.update_record(c.a.id, mapping.key, row.id, row.revision, {"target": ""})
    first = publish_table(c, mapping)
    c.s.dataset_versions.create_concept(c.a.id, mapping.id)
    row = rows(c, mapping)["a"]
    c.s.dataset_records.update_record(c.a.id, mapping.key, row.id, row.revision, {"target": "A"})
    card = attention(overview.read(c.a.id, all_owners=True), mapping.id)
    assert reason(card, 8).draft
    clean = publish_table(c, mapping, NOW.date() + timedelta(days=1))
    card = attention(overview.read(c.a.id, all_owners=True), mapping.id)
    assert reason(card, 8).version_id == first.id
    c.s.datasets.uow.clock.advance(days=1)
    c.s.dataset_versions.create_concept(c.a.id, mapping.id)
    row = rows(c, mapping)["a"]
    c.s.dataset_records.update_record(c.a.id, mapping.key, row.id, row.revision, {"target": ""})
    dirty = publish_table(c, mapping, NOW.date() + timedelta(days=2))
    card = attention(overview.read(c.a.id, all_owners=True), mapping.id)
    assert reason(card, 8).version_id == dirty.id
    assert card.version.id == clean.id
    c.s.dataset_versions.cancel(c.a.id, mapping.id, dirty.id)
    assert all(
        r.number != 8
        for card in overview.read(c.a.id, all_owners=True).attention
        for r in card.reasons
    )


def test_recent_visits_are_private_bounded_and_not_audited(database_context):
    c = database_context
    overview = service(c)
    created = []
    for i in range(22):
        c.s.datasets.uow.clock.advance(seconds=1)
        dataset = c.s.datasets.create_dataset(c.a.id, f"data_{i}", f"Dataset {i}", "", c.owner.id)
        overview.record_visit(c.a.id, key=dataset.key)
        created.append(dataset)
    before = {a.id for a in audits(c)}
    overview.record_visit(c.a.id, key=created[0].key)
    assert {a.id for a in audits(c)} == before
    with c.sessions() as session:
        visits = list(session.scalars(select(DatasetVisit)))
        assert len(visits) == 20
        assert {v.user_id for v in visits} == {c.a.id}
    view = overview.read(c.a.id)
    assert len(view.visited) == len(view.created) == 4
    assert view.visited[0].dataset.id == created[0].id
    assert {card.dataset.id for card in view.created} == {d.id for d in created[-4:]}
    assert not overview.read(c.u.id).visited
    # A visit never grants Draft access; visibility is evaluated on each read.
    c.s.memberships.create_membership(c.a.id, c.u.id, c.owner.id)
    overview.record_visit(c.u.id, key=created[-1].key)
    assert len(overview.read(c.u.id).visited) == 1
    c.s.datasets.change_owner(c.a.id, created[-1].key, c.owner.id, c.other.id)
    assert not overview.read(c.u.id).visited


def test_attention_query_count_does_not_grow_per_dataset(database_context):
    c = database_context
    overview = service(c)
    age_draft(c, c.d.id, 20)
    queries = []

    def collect(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(c.engine, "before_cursor_execute", collect)
    try:
        overview.read(c.a.id, all_owners=True)
        first = len(queries)
        for i in range(15):
            c.s.datasets.create_dataset(c.a.id, f"many_{i}", "Many", "", c.owner.id)
        queries.clear()
        overview.read(c.a.id, all_owners=True)
        assert len(queries) == first
    finally:
        event.remove(c.engine, "before_cursor_execute", collect)


def test_overview_browser_navigation_and_filters(dataset_browser):
    sessions, services, admin, client = dataset_browser
    dataset = services.datasets.create_dataset(
        admin.id, "sample", "<Sample>", "A description", services.teams.list_teams(admin.id)[0].id
    )
    with sessions.begin() as session:
        session.get(DatasetConcept, dataset.id).updated_at -= timedelta(days=15)
    login(client)
    page = request(client, "get", "/")
    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    assert "You're not in a team yet" in body(page)
    assert 'role="switch"' in body(page)
    page = request(client, "get", "/?all_owners=1")
    assert "Drafts untouched for 14 days" in body(page)
    assert "&lt;Sample&gt;" in body(page)
    page = request(client, "get", "/attention?reason=6&all_owners=1")
    assert page.status_code == 200
    assert f'href="/datasets/{dataset.key}/concept/records"' in body(page)
    assert "Draft untouched for 15 days" in body(page)
    assert request(client, "get", "/attention?reason=").status_code == 200
    assert request(client, "get", "/attention?reason=9").status_code == 400
    assert request(client, "get", "/attention?all_owners=1&all_owners=1").status_code == 400
    for path in (
        f"/datasets/{dataset.key}/concept/records",
        f"/datasets/{dataset.key}/concept?tab=dataset-details",
    ):
        assert request(client, "get", path).status_code == 200
    with sessions() as session:
        assert len(list(session.scalars(select(DatasetVisit)))) == 1
    assert "&lt;Sample&gt;" in body(request(client, "get", "/"))


def test_mapping_attention_queries_are_batched(database_context):
    from test_mapping_source_versions import create_mapping

    c = database_context
    first, _, _ = mapping_fixture(c)
    overview = service(c)
    next_source(c)
    queries = []

    def collect(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(c.engine, "before_cursor_execute", collect)
    try:
        assert attention(overview.read(c.a.id, all_owners=True), first.id)
        count = len(queries)
        for i in range(8):
            mapping = create_mapping(c, f"extra_{i}")
            c.s.dataset_fields.create_field(c.a.id, mapping.key, "target", "Target", "string")
            publish_table(c, mapping)
        queries.clear()
        result = overview.read(c.a.id, all_owners=True)
        assert len(queries) == count
        assert len([card for card in result.attention if card.dataset.kind == "mapping"]) == 9
    finally:
        event.remove(c.engine, "before_cursor_execute", collect)


def test_parallel_visits_keep_one_row_and_atomic_cap(database_context):
    from concurrent.futures import ThreadPoolExecutor

    c = database_context
    overview = service(c)
    datasets = [
        c.s.datasets.create_dataset(c.a.id, f"parallel_{i}", "Parallel", "", c.owner.id)
        for i in range(22)
    ]
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda dataset: overview.record_visit(c.a.id, key=dataset.key), datasets))
        list(workers.map(lambda _: overview.record_visit(c.a.id, key=c.d.key), range(6)))
    with c.sessions() as session:
        visits = list(session.scalars(select(DatasetVisit)))
        assert len(visits) == 20
        assert len([visit for visit in visits if visit.dataset_id == c.d.id]) == 1
    assert overview.read(c.a.id).visited[0].dataset.id == c.d.id


def test_visit_write_rolls_back_and_does_not_replace_page(dataset_browser, monkeypatch):
    from refrncr.overview_persistence import SqlAlchemyOverviewOperations
    from refrncr.ports import PersistenceConflictError

    sessions, services, admin, client = dataset_browser
    dataset = services.datasets.create_dataset(
        admin.id, "visit", "Visit", "", services.teams.list_teams(admin.id)[0].id
    )
    login(client)
    original = SqlAlchemyOverviewOperations.record_visit

    def conflict(self, *args):
        original(self, *args)
        raise PersistenceConflictError()

    monkeypatch.setattr(SqlAlchemyOverviewOperations, "record_visit", conflict)
    assert request(client, "get", f"/datasets/{dataset.key}/concept/records").status_code == 200
    with sessions() as session:
        assert list(session.scalars(select(DatasetVisit))) == []


def test_unpublished_datasets_are_not_missing_current_errors(database_context):
    c = database_context
    overview = service(c)
    assert not overview.read(c.a.id, all_owners=True).attention
    assert not overview.read(c.u.id).created  # no other-team Draft metadata
