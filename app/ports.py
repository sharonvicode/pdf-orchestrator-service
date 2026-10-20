"""Puertos hacia los microservicios que coordina el Orchestrator.

Son contratos INTERNOS: los adapters de app/adapters/ los traducen a los contratos HTTP
de cada servicio. Cualquier implementación puede además lanzar ServicioNoDisponibleError
o TiempoAgotadoError.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TextoExtraido:
    texto: str
    cantidad_paginas: int


class Validator(Protocol):
    async def validar(self, nombre_archivo: str, contenido: bytes) -> None:
        """Termina sin error si el archivo es válido; si no, lanza ArchivoRechazadoError."""
        ...


class Extractor(Protocol):
    async def extraer(self, nombre_archivo: str, contenido: bytes) -> TextoExtraido:
        """Devuelve el texto y las páginas del PDF.

        Lanza ArchivoRechazadoError si el archivo no se puede procesar, o ExtraccionFallidaError si falla.
        """
        ...


class Persistence(Protocol):
    async def guardar(self, nombre_archivo: str, texto: str, cantidad_paginas: int) -> str:
        """Persiste la extracción y devuelve el id generado, o lanza PersistenciaFallidaError."""
        ...
