import uvicorn
from fastapi import FastAPI

from app.config import Settings
from app.problem_details import register_problem_handlers
from app.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="PDF Orchestrator Service")
    register_problem_handlers(app)
    app.include_router(router)
    return app


app = create_app()


def run() -> None:
    """Levanta el servidor con la configuración validada; falla antes de arrancar si está incompleta."""
    settings = Settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    run()
