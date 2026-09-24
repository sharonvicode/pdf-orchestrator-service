"""DTOs públicos del Orchestrator."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class ExtraccionResponse(BaseModel):
    exito: bool
    texto: str
    nombre_archivo: str
