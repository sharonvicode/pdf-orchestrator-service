"""Composición de dependencias para FastAPI."""

from typing import Annotated

from fastapi import Depends, Request

from app.adapters.extractor import HttpExtractor
from app.adapters.http import ClientesHttp
from app.adapters.persistence import HttpPersistence
from app.adapters.validator import HttpValidator
from app.services.orchestrator import OrchestratorService


def get_clientes(request: Request) -> ClientesHttp:
    """Clientes HTTP compartidos que abre el lifespan."""
    return request.app.state.clientes


def get_orchestrator_service(clientes: Annotated[ClientesHttp, Depends(get_clientes)]) -> OrchestratorService:
    return OrchestratorService(
        validator=HttpValidator(clientes.validator),
        extractor=HttpExtractor(clientes.extractor),
        persistence=HttpPersistence(clientes.persistence),
    )
