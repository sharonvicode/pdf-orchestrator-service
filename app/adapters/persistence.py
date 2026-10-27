"""Adapter HTTP de Persistence: POST /extractions con el texto extraído como JSON.

Persistence no garantiza RFC 9457: sus errores pueden ser JSON o texto plano. Por eso su
cuerpo de error no se interpreta ni se expone; cualquier respuesta fuera de contrato es 502.
El alta no es idempotente: no se reintenta.
"""

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.adapters.http import errores_de_transporte
from app.exceptions import PersistenciaFallidaError

SERVICIO = "Persistence"
FUERA_DE_CONTRATO = "Persistence respondió fuera del contrato esperado."


class _Creado(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str


class HttpPersistence:
    def __init__(self, cliente: httpx.AsyncClient):
        self._cliente = cliente

    async def guardar(self, nombre_archivo: str, texto: str, cantidad_paginas: int) -> str:
        cuerpo = {"file_name": nombre_archivo, "text": texto, "page_count": cantidad_paginas}
        with errores_de_transporte(SERVICIO):
            respuesta = await self._cliente.post("/extractions", json=cuerpo)

        id_ = _id_creado(respuesta)
        if id_ is None:
            raise PersistenciaFallidaError(FUERA_DE_CONTRATO)
        return id_


def _id_creado(respuesta: httpx.Response) -> str | None:
    """El id de un 201 que cumple el contrato (texto no vacío); None en cualquier otro caso."""
    if respuesta.status_code != 201:
        return None
    try:
        creado = _Creado.model_validate_json(respuesta.content)
    except ValidationError:
        return None
    return creado.id if creado.id.strip() else None
