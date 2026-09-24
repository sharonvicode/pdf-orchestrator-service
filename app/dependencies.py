"""Composición de dependencias para FastAPI."""

from app.adapters.fakes import FakeExtractor, FakePersistence, FakeValidator
from app.services.orchestrator import OrchestratorService


def get_orchestrator_service() -> OrchestratorService:
    # TODO: reemplazar los fakes por los clientes HTTP reales de cada microservicio.
    return OrchestratorService(
        validator=FakeValidator(),
        extractor=FakeExtractor(),
        persistence=FakePersistence(),
    )
