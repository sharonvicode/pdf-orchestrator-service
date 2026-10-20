"""Composición de dependencias para FastAPI."""

from typing import Annotated

from fastapi import Depends, Request

from app.adapters.fakes import FakeExtractor, FakePersistence
from app.adapters.http import ClientesHttp
from app.adapters.validator import HttpValidator
from app.services.orchestrator import OrchestratorService


def get_clientes(request: Request) -> ClientesHttp:
    """Clientes HTTP compartidos que abre el lifespan."""
    return request.app.state.clientes


def get_orchestrator_service(clientes: Annotated[ClientesHttp, Depends(get_clientes)]) -> OrchestratorService:
    # TODO: reemplazar los fakes de Extractor y Persistence por sus adapters HTTP.
    return OrchestratorService(
        validator=HttpValidator(clientes.validator),
        extractor=FakeExtractor(),
        persistence=FakePersistence(),
    )
