"""Puertos hacia los microservicios que coordina el Orchestrator."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResultadoValidacion:
    es_valido: bool
    motivo: str | None = None


class Validator(Protocol):
    async def validar(self, nombre_archivo: str, contenido: bytes) -> ResultadoValidacion: ...


class Extractor(Protocol):
    async def extraer(self, nombre_archivo: str, contenido: bytes) -> str:
        """Devuelve el texto del PDF o lanza ExtraccionFallidaError."""
        ...


class Persistence(Protocol):
    async def guardar(self, nombre_archivo: str, texto: str) -> None:
        """Persiste el texto o lanza PersistenciaFallidaError."""
        ...
