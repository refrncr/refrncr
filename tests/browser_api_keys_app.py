"""Disposable REF-40 browser fixture; only synthetic local test credentials."""

import logging

from sqlalchemy.pool import StaticPool

from refrncr.api_keys import ApiKeyService
from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.services import ResourceTypeRegistry
from refrncr.web import create_flask_app

engine = create_database_engine(
    "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
)
Base.metadata.create_all(engine)
services = create_community_services(create_session_factory(engine), ResourceTypeRegistry())
admin = services.users.create_initial_admin(
    "admin@example.com", "Admin", "correct horse battery staple"
)
services.users.create_user(admin.id, "user@example.com", "User", "correct horse battery staple")
keys = ApiKeyService(services.users.uow, pepper=b"disposable-browser-fixture-pepper")
app = create_flask_app(
    services.users,
    services.authentication,
    services.sessions,
    services.teams,
    services.memberships,
    api_keys=keys,
    allow_loopback_http=True,
)

if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="127.0.0.1", port=5140, threaded=False, use_reloader=False)
