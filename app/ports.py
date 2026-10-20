"""Puertos hacia los microservicios que coordina el Orchestrator.

Son contratos INTERNOS: los adapters de app/adapters/ los traducen a los contratos HTTP
de cada servicio. Cualquier implementación puede además lanzar ServicioNoDisponibleError
o TiempoAgotadoError.
"""

from typing import Protocol


class Validator(Protocol):
    async def validar(self, nombre_archivo: str, contenido: bytes) -> None:
        """Termina sin error si el archivo es válido; si no, lanza ArchivoRechazadoError."""
        ...


class Extractor(Protocol):
    async def extraer(self, nombre_archivo: str, contenido: bytes) -> str:
        """Devuelve el texto del PDF o lanza ExtraccionFallidaError."""
        ...


class Persistence(Protocol):
    async def guardar(self, nombre_archivo: str, texto: str) -> None:
        """Persiste el texto o lanza PersistenciaFallidaError."""
        ...
