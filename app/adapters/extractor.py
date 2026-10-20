"""Adapter HTTP del Extractor: POST /api/v1/extraer con el archivo en el campo multipart `file`."""

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.http import archivo_pdf, errores_de_transporte, leer_problema, no_disponible, rechazo_del_archivo
from app.exceptions import ArchivoRechazadoError, ExtraccionFallidaError
from app.ports import TextoExtraido

SERVICIO = "Extractor"
FUERA_DE_CONTRATO = "El Extractor respondió fuera del contrato esperado."

# 413 (archivo demasiado grande) y 422 (PDF que no se puede procesar) son rechazos del archivo.
STATUS_DE_RECHAZO = {413, 422}


class _Metadatos(BaseModel):
    model_config = ConfigDict(strict=True)

    cantidad_paginas: int = Field(ge=0)


class _Exito(BaseModel):
    model_config = ConfigDict(strict=True)

    texto: str
    metadatos: _Metadatos


class HttpExtractor:
    def __init__(self, cliente: httpx.AsyncClient):
        self._cliente = cliente

    async def extraer(self, nombre_archivo: str, contenido: bytes) -> TextoExtraido:
        with errores_de_transporte(SERVICIO):
            respuesta = await self._cliente.post("/api/v1/extraer", files=archivo_pdf(nombre_archivo, contenido))

        if respuesta.status_code == 200:
            return _texto_extraido(respuesta)
        if respuesta.status_code == 503:
            raise no_disponible(SERVICIO, retry_after=respuesta.headers.get("Retry-After"))
        raise _rechazo(respuesta) or ExtraccionFallidaError(FUERA_DE_CONTRATO)


def _texto_extraido(respuesta: httpx.Response) -> TextoExtraido:
    try:
        exito = _Exito.model_validate_json(respuesta.content)
    except ValidationError:
        raise ExtraccionFallidaError(FUERA_DE_CONTRATO) from None
    return TextoExtraido(texto=exito.texto, cantidad_paginas=exito.metadatos.cantidad_paginas)


def _rechazo(respuesta: httpx.Response) -> ArchivoRechazadoError | None:
    """Traduce un rechazo del archivo que cumple el contrato; None si no lo cumple.

    Un 422 con la extensión `errors` es la validación del request del Extractor: lo armó
    mal el Orchestrator, no es un problema del archivo.
    """
    problema = leer_problema(respuesta)
    if respuesta.status_code not in STATUS_DE_RECHAZO or problema is None or "errors" in problema:
        return None
    return rechazo_del_archivo(respuesta.status_code, "about:blank", problema)
