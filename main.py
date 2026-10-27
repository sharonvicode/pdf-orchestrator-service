from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.adapters.http import abrir_clientes
from app.config import Settings
from app.problem_details import register_problem_handlers
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Comparte un cliente HTTP por servicio durante la vida de la app y los cierra al apagar."""
    async with abrir_clientes(Settings()) as clientes:
        app.state.clientes = clientes
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="PDF Orchestrator Service", lifespan=lifespan)
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
