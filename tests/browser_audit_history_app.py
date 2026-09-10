"""Disposable in-memory REF-39 browser fixture; never point at application data."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from test_dataset_browser import dataset_browser

from refrncr.models import AuditEvent, User
from refrncr.web import create_flask_app

factory, s, admin, _ = dataset_browser.__wrapped__()
with factory.begin() as session:
    session.get(User, admin.id).display_name = "Administrator — " + "Long display name " * 5
    for i in range(125):
        session.add(
            AuditEvent(
                id=str(UUID(int=1000 + i)),
                actor_user_id=admin.id,
                timestamp=datetime(2026, 3, 29, 0, 59, 0, 123456, tzinfo=UTC)
                + timedelta(seconds=(i // 3) * 3),
                object_type="dataset_record",
                object_id=str(UUID(int=2000 + i)),
                action="updated",
                details={"values": "secret-business-value", "password": "secret-password"},
            )
        )
app = create_flask_app(
    s.users,
    s.authentication,
    s.sessions,
    s.teams,
    s.memberships,
    audit_history=s.audit_history,
    allow_loopback_http=True,
)
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5145, threaded=True, use_reloader=False)
