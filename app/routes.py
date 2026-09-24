"""Capa de presentación: endpoints HTTP del Orchestrator."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from app.dependencies import get_orchestrator_service
from app.schemas import ExtraccionResponse, HealthResponse
from app.services.orchestrator import OrchestratorService

router = APIRouter()


@router.get("/health/", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Indica que el Orchestrator está vivo. No consulta otros servicios."""
    return HealthResponse(status="ok")


@router.post("/extraer", response_model=ExtraccionResponse)
async def extraer(
    file: Annotated[UploadFile, File()],
    servicio: Annotated[OrchestratorService, Depends(get_orchestrator_service)],
) -> ExtraccionResponse:
    """Recibe el archivo y delega todo el flujo en el OrchestratorService."""
    contenido = await file.read()
    resultado = await servicio.procesar(file.filename or "", contenido)
    return ExtraccionResponse(exito=True, texto=resultado.texto, nombre_archivo=resultado.nombre_archivo)
