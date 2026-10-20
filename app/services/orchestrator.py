"""Caso de uso: coordina Validator → Extractor → Persistence."""

from dataclasses import dataclass

from app.ports import Extractor, Persistence, Validator


@dataclass(frozen=True)
class ResultadoExtraccion:
    id: str
    nombre_archivo: str
    texto: str


class OrchestratorService:
    def __init__(self, validator: Validator, extractor: Extractor, persistence: Persistence):
        self._validator = validator
        self._extractor = extractor
        self._persistence = persistence

    async def procesar(self, nombre_archivo: str, contenido: bytes) -> ResultadoExtraccion:
        await self._validator.validar(nombre_archivo, contenido)
        extraido = await self._extractor.extraer(nombre_archivo, contenido)
        id_ = await self._persistence.guardar(nombre_archivo, extraido.texto, extraido.cantidad_paginas)
        return ResultadoExtraccion(id=id_, nombre_archivo=nombre_archivo, texto=extraido.texto)
