"""Infraestructura HTTP común a los adapters: clientes compartidos y errores de transporte."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import ArchivoRechazadoError, ServicioNoDisponibleError, TiempoAgotadoError
from app.problem_details import PROBLEM_JSON

PDF_MIME_TYPE = "application/pdf"


@dataclass(frozen=True)
class ClientesHttp:
    validator: httpx.AsyncClient
    extractor: httpx.AsyncClient
    persistence: httpx.AsyncClient


@asynccontextmanager
async def abrir_clientes(settings: Settings) -> AsyncIterator[ClientesHttp]:
    """Abre un cliente por servicio y los cierra al salir."""
    timeout = settings.http_timeout_seconds
    async with (
        httpx.AsyncClient(base_url=str(settings.validator_url), timeout=timeout) as validator,
        httpx.AsyncClient(base_url=str(settings.extractor_url), timeout=timeout) as extractor,
        httpx.AsyncClient(base_url=str(settings.persistence_url), timeout=timeout) as persistence,
    ):
        yield ClientesHttp(validator=validator, extractor=extractor, persistence=persistence)


def leer_problema(respuesta: httpx.Response) -> dict[str, Any] | None:
    """Devuelve el cuerpo RFC 9457 de la respuesta, o None si no es un objeto application/problem+json."""
    if not respuesta.headers.get("content-type", "").startswith(PROBLEM_JSON):
        return None
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None
    return cuerpo if isinstance(cuerpo, dict) else None


def archivo_pdf(nombre_archivo: str, contenido: bytes) -> dict[str, tuple[str, bytes, str]]:
    """Campo multipart `file` con el archivo, siempre enviado como application/pdf."""
    return {"file": (nombre_archivo, contenido, PDF_MIME_TYPE)}


def rechazo_del_archivo(status_code: int, type_: str, problema: dict[str, Any]) -> ArchivoRechazadoError | None:
    """Rechazo con el title y detail del problema, o None si alguno no es texto."""
    title, detail = problema.get("title"), problema.get("detail")
    if not (isinstance(title, str) and isinstance(detail, str)):
        return None
    return ArchivoRechazadoError(status_code, type_, title, detail)


def no_disponible(servicio: str, retry_after: str | None = None) -> ServicioNoDisponibleError:
    return ServicioNoDisponibleError(f"El servicio {servicio} no está disponible.", retry_after=retry_after)


@contextmanager
def errores_de_transporte(servicio: str) -> Iterator[None]:
    """Traduce los fallos de transporte de httpx a errores de dominio con mensajes fijos.

    TimeoutException es subclase de TransportError, por eso se captura primero.
    """
    try:
        yield
    except httpx.TimeoutException as error:
        raise TiempoAgotadoError(f"El servicio {servicio} no respondió a tiempo.") from error
    except httpx.TransportError as error:
        raise no_disponible(servicio) from error
