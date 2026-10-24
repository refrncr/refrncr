"""Authorized Overview reads; attention is derived from existing lifecycle contracts."""

from dataclasses import dataclass, field
from datetime import timedelta

from refrncr.mapping_updates import source_update_blocker
from refrncr.services import AuthorizationError
from refrncr.version_contracts import version_states

REASON_LABELS = {
    1: "Source update available",
    2: "Source update blocked",
    3: "No Current version",
    4: "Scheduled source update",
    5: "Scheduled version coming up",
    6: "Draft untouched for 14 days",
    7: "Incomplete mapping draft",
    8: "Published mapping with unmapped rows",
}
TALLY_LABELS = {
    6: "Drafts untouched for 14 days",
    7: "Incomplete mapping drafts",
    8: "Published mappings with unmapped rows",
}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def display_date(value):
    return f"{value.day} {value.strftime('%b %Y')}"


def effect_timing(days):
    return "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"


def row_count(count, description):
    return f"{count} {description} {'row' if count == 1 else 'rows'}"


@dataclass(frozen=True)
class AttentionReason:
    number: int
    severity: str
    text: str
    version_id: str | None = None
    draft: bool = False
    mapping_status: str | None = None
    note: str | None = None

    @property
    def label(self):
        return REASON_LABELS[self.number]

    @property
    def priority(self):
        return SEVERITY_ORDER[self.severity], self.number

    @property
    def icon(self):
        return {"error": "circle-x", "warning": "triangle-alert", "info": "calendar-clock"}[
            self.severity
        ]


@dataclass
class OverviewCard:
    dataset: object
    version: object | None
    record_count: int
    last_updated: object
    has_draft: bool
    reasons: list[AttentionReason] = field(default_factory=list)
    may_export: bool = False

    @property
    def primary(self):
        return min(self.reasons, key=lambda reason: reason.priority) if self.reasons else None

    @property
    def card_reason(self):
        return min(
            (r for r in self.reasons if r.number <= 5), key=lambda r: r.priority, default=None
        )

    @property
    def group(self):
        reason = self.card_reason
        return "Needs action" if reason and reason.number <= 3 else "Coming up" if reason else None

    def reason_for_display(self, number=None):
        """A filtered work list takes precedence over the card's usual headline."""
        if number is not None:
            return next(reason for reason in self.reasons if reason.number == number)
        return self.card_reason or self.primary


@dataclass
class Overview:
    attention: list[OverviewCard]
    visited: list[OverviewCard]
    created: list[OverviewCard]
    is_admin: bool
    has_team: bool
    all_owners: bool
    today: object | None = None

    @property
    def tallies(self):
        return [
            (
                number,
                TALLY_LABELS[number],
                sum(
                    any(reason.number == number for reason in card.reasons)
                    for card in self.attention
                ),
            )
            for number in (6, 7, 8)
        ]


class OverviewService:
    def __init__(self, uow, authorization):
        self.uow = uow
        self.authorization = authorization

    def record_visit(self, actor_id, *, key=None, dataset_id=None):
        """Navigation telemetry, not a business mutation or an authorization grant."""
        with self.uow(write=True, serialized=True, translate_errors=True) as work:
            actor = work.users.get(actor_id, for_update=True)
            if not self.authorization.active(actor):
                raise AuthorizationError("Active user access required")
            dataset = work.datasets.find_by_key(key) if key else work.datasets.get(dataset_id)
            if dataset is not None:
                work.overview.record_visit(actor_id, dataset.id, work.now())

    def read(self, actor_id, *, all_owners=False):
        with self.uow(translate_errors=True) as work:
            work.begin_export_snapshot()
            actor = work.users.get(actor_id)
            if not self.authorization.active(actor):
                raise AuthorizationError("Active user access required")
            admin = actor.role == "admin"
            if all_owners and not admin:
                raise AuthorizationError("Only administrators can show all owners")
            teams = set(work.memberships.team_ids_for_user(actor_id))
            visible = self.authorization.viewable_resources(actor_id, uow=work)
            maintenance = self.authorization.maintainable_resources(actor_id, uow=work)
            rows = work.datasets.current_overview(
                None if visible.all_resources else visible.resource_ids
            )
            maintain = {
                row.dataset.id
                for row in rows
                if maintenance.all_resources or row.dataset.id in maintenance.resource_ids
            }
            versions = work.versions.list_for_datasets(tuple(row.dataset.id for row in rows))
            states = {key: version_states(items, work.today()) for key, items in versions.items()}
            current = {
                key: next((s.version for s in items if s.status == "Current"), None)
                for key, items in states.items()
            }
            concepts = work.concepts.list_for_datasets(tuple(maintain))
            hierarchy_counts = work.versions.hierarchy_node_counts(
                tuple(
                    current[row.dataset.id].id
                    for row in rows
                    if row.dataset.kind == "hierarchy" and current.get(row.dataset.id)
                )
            )
            cards = {}
            for row in rows:
                dataset = row.dataset
                version = current.get(dataset.id)
                if version is None and dataset.id not in maintain:
                    continue
                cards[dataset.id] = OverviewCard(
                    dataset,
                    version,
                    hierarchy_counts.get(version.id, version.record_count) if version else 0,
                    version.published_at if version else dataset.created_at,
                    dataset.id in concepts,
                )
            owned = [
                card for card in cards.values() if all_owners or card.dataset.owner_team_id in teams
            ]
            self._attention(work, owned, states, concepts)
            attention = sorted(
                (c for c in owned if c.reasons),
                key=lambda c: (c.primary.priority, c.dataset.name.casefold(), c.dataset.id),
            )
            visited = [cards[key] for key in work.overview.recent_ids(actor_id) if key in cards][:4]
            for card in visited:
                card.may_export = (
                    card.version is not None
                    and self.authorization.can_export_dataset(actor_id, card.dataset.id, uow=work)
                )
            created = sorted(
                cards.values(), key=lambda c: (c.dataset.created_at, c.dataset.id), reverse=True
            )[:4]
            return Overview(
                attention, visited, created, admin, bool(teams), all_owners, work.today()
            )

    def _attention(self, work, cards, states, concepts):
        mappings = [c for c in cards if c.dataset.kind == "mapping"]
        draft_counts = work.overview.draft_counts(
            tuple(c.dataset.id for c in mappings if c.has_draft)
        )
        versions = {s.version.id: s.version for items in states.values() for s in items}
        source_ids = {c.dataset.source_dataset_id for c in mappings}
        source_versions = work.versions.list_for_datasets(tuple(source_ids))
        source_states = {
            key: version_states(items, work.today()) for key, items in source_versions.items()
        }
        for items in source_versions.values():
            versions.update((version.id, version) for version in items)
        fields = work.overview.version_fields(
            tuple(v.id for items in source_versions.values() for v in items)
        )
        published_counts = work.overview.version_unmapped(
            tuple(
                state.version.id
                for card in mappings
                for state in states.get(card.dataset.id, ())
                if state.status in {"Current", "Scheduled"}
            )
        )
        for card in cards:
            dataset = card.dataset
            lifecycle = states.get(dataset.id, ())
            concept = concepts.get(dataset.id)
            if card.version is None:
                expired = [s for s in lifecycle if s.status == "Expired"]
                if expired:
                    end = max(s.valid_until for s in expired)
                    card.reasons.append(
                        AttentionReason(
                            3,
                            "error",
                            f"No Current version since {display_date(end)}",
                            draft=card.has_draft,
                            note="Consumers get no records.",
                        )
                    )
            for state in lifecycle:
                if (
                    state.status == "Scheduled"
                    and 0 <= (state.version.effective_date - work.today()).days <= 7
                ):
                    date = state.version.effective_date
                    days = (date - work.today()).days
                    card.reasons.append(
                        AttentionReason(
                            5,
                            "info",
                            f"V{state.version.number} takes effect {effect_timing(days)}",
                            state.version.id,
                            note=f"Effective {display_date(date)}. Check or cancel it before then.",
                        )
                    )
            if concept and work.now() - concept.updated_at >= timedelta(days=14):
                days = (work.now() - concept.updated_at).days
                card.reasons.append(
                    AttentionReason(6, "warning", f"Draft untouched for {days} days.", draft=True)
                )
            if dataset.kind == "mapping":
                self._mapping_reasons(
                    card,
                    lifecycle,
                    source_states.get(dataset.source_dataset_id, ()),
                    versions,
                    fields,
                    draft_counts,
                    published_counts,
                )
            # One reason per category, even when several Scheduled versions qualify.
            card.reasons = list({r.number: r for r in reversed(card.reasons)}.values())
            card.reasons.sort(key=lambda reason: reason.priority)

    @staticmethod
    def _mapping_reasons(
        card, lifecycle, sources, versions, fields, draft_counts, published_counts
    ):
        dataset = card.dataset
        selected = card.version or next(
            (s.version for s in lifecycle if s.status == "Scheduled"), None
        )
        pinned = versions.get(selected.source_version_id if selected else dataset.source_version_id)
        draft_source = versions.get(dataset.source_version_id) if card.has_draft else None
        latest = max(
            (s.version for s in sources if s.status != "Cancelled"),
            key=lambda v: v.number,
            default=None,
        )
        if pinned and latest and latest.number > pinned.number:
            already = draft_source is not None and draft_source.number >= latest.number
            identifier = next(
                (f for f in fields.get(pinned.id, ()) if f.key == dataset.source_field_key), None
            )
            blocker = (
                source_update_blocker(
                    dataset,
                    latest,
                    fields.get(latest.id, ()),
                    identifier.data_type if identifier else None,
                )
                if not already
                else None
            )
            if blocker:
                card.reasons.append(
                    AttentionReason(
                        2,
                        "error",
                        f"Can't update to {latest.name} V{latest.number}",
                        selected.id if selected else None,
                        draft=selected is None,
                        note=blocker,
                    )
                )
            for source in sources:
                if (
                    source.status not in {"Current", "Scheduled"}
                    or source.version.number <= pinned.number
                ):
                    continue
                target = source.version
                fixed = draft_source is not None and draft_source.number >= target.number
                if source.status == "Current":
                    text = f"Uses V{pinned.number} · {target.name} V{target.number} is Current"
                    number, severity = 1, "warning"
                else:
                    text = f"Uses V{pinned.number} · {target.name} V{target.number} is Scheduled"
                    number, severity = 4, "info"
                card.reasons.append(
                    AttentionReason(
                        number,
                        severity,
                        text,
                        selected.id if selected else None,
                        draft=fixed or selected is None,
                        note=f"Draft already uses V{draft_source.number}. Review and publish."
                        if fixed
                        else f"Takes effect on {display_date(target.effective_date)}."
                        if source.status == "Scheduled"
                        else None,
                    )
                )
        unmapped, removed = draft_counts.get(dataset.id, (0, 0))
        if card.has_draft and (unmapped or removed):
            card.reasons.append(
                AttentionReason(
                    7,
                    "warning",
                    " · ".join(
                        row_count(count, label)
                        for count, label in ((unmapped, "unmapped"), (removed, "removed"))
                        if count
                    ),
                    draft=True,
                    mapping_status="unmapped" if unmapped else "removed",
                    note="Rows removed from the source are still in the Draft."
                    if removed
                    else None,
                )
            )
        for state in sorted(
            lifecycle, key=lambda s: (s.status != "Current", s.version.effective_date)
        ):
            count = published_counts.get(state.version.id, 0)
            if state.status in {"Current", "Scheduled"} and count:
                fixed = card.has_draft and unmapped == 0
                text = f"V{state.version.number} ({state.status}) · {row_count(count, 'unmapped')}"
                card.reasons.append(
                    AttentionReason(
                        8,
                        "warning",
                        text,
                        state.version.id,
                        draft=fixed,
                        mapping_status=None if fixed else "unmapped",
                        note="The Draft has no unmapped rows. Review and publish."
                        if fixed
                        else None,
                    )
                )
