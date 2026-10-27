"""Adapter HTTP del Validator: POST /validate con el archivo en el campo multipart `file`."""

import httpx

from app.adapters.http import archivo_pdf, errores_de_transporte, leer_problema, no_disponible, rechazo_del_archivo
from app.exceptions import ArchivoRechazadoError, ValidacionFallidaError

SERVICIO = "Validator"

# Rechazos del archivo que se propagan al cliente. /errors/bad-request no está: indica
# un request mal armado por el Orchestrator, no un problema del archivo.
TIPOS_DE_RECHAZO = {
    "/errors/empty-file",
    "/errors/file-too-large",
    "/errors/unsupported-file-type",
    "/errors/invalid-pdf",
}
STATUS_DE_RECHAZO = {400, 413, 415, 422}


class HttpValidator:
    def __init__(self, cliente: httpx.AsyncClient):
        self._cliente = cliente

    async def validar(self, nombre_archivo: str, contenido: bytes) -> None:
        with errores_de_transporte(SERVICIO):
            respuesta = await self._cliente.post("/validate", files=archivo_pdf(nombre_archivo, contenido))

        if respuesta.status_code == 200:
            return
        if respuesta.status_code == 503:
            raise no_disponible(SERVICIO)
        raise _rechazo(respuesta) or ValidacionFallidaError("El Validator respondió fuera del contrato esperado.")


def _rechazo(respuesta: httpx.Response) -> ArchivoRechazadoError | None:
    """Traduce un rechazo del archivo que cumple el contrato; None si no lo cumple."""
    problema = leer_problema(respuesta)
    if respuesta.status_code not in STATUS_DE_RECHAZO or problema is None:
        return None

    type_ = problema.get("type")
    if not isinstance(type_, str) or type_ not in TIPOS_DE_RECHAZO:
        return None
    return rechazo_del_archivo(respuesta.status_code, type_, problema)
