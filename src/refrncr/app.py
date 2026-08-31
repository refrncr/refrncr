from a2wsgi import WSGIMiddleware
from fastapi import FastAPI

from refrncr.services import SessionService, UserService
from refrncr.web import create_flask_app


def create_application(users: UserService, sessions: SessionService) -> FastAPI:
    users.bootstrap_organisation()
    app = FastAPI(title="refrncr")

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    flask_app = create_flask_app(users, sessions)
    app.mount("/", WSGIMiddleware(flask_app))
    return app
