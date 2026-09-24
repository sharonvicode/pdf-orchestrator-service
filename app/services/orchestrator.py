"""Caso de uso: coordina Validator → Extractor → Persistence."""

from dataclasses import dataclass

from app.exceptions import PdfInvalidoError
from app.ports import Extractor, Persistence, Validator


@dataclass(frozen=True)
class ResultadoExtraccion:
    nombre_archivo: str
    texto: str


class OrchestratorService:
    def __init__(self, validator: Validator, extractor: Extractor, persistence: Persistence):
        self._validator = validator
        self._extractor = extractor
        self._persistence = persistence

    async def procesar(self, nombre_archivo: str, contenido: bytes) -> ResultadoExtraccion:
        validacion = await self._validator.validar(nombre_archivo, contenido)
        if not validacion.es_valido:
            raise PdfInvalidoError(validacion.motivo or "El archivo no es un PDF válido.")

        texto = await self._extractor.extraer(nombre_archivo, contenido)
        await self._persistence.guardar(nombre_archivo, texto)
        return ResultadoExtraccion(nombre_archivo=nombre_archivo, texto=texto)
