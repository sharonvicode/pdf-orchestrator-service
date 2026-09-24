from fastapi import FastAPI

from app.problem_details import register_problem_handlers
from app.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="PDF Orchestrator Service")
    register_problem_handlers(app)
    app.include_router(router)
    return app


app = create_app()
