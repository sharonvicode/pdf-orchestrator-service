"""Infraestructura HTTP común a los adapters: clientes compartidos y errores de transporte."""

import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import ArchivoRechazadoError, ServicioNoDisponibleError, TiempoAgotadoError
from app.problem_details import PROBLEM_JSON

PDF_MIME_TYPE = "application/pdf"

# Retry-After (RFC 9110 §10.2.3): delay-seconds o IMF-fixdate, el único formato de fecha
# que un emisor debe generar (§5.6.7).
DELAY_SECONDS = re.compile(r"[0-9]+")
IMF_FIXDATE = re.compile(
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT"
)


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


def rechazo_del_archivo(
    status_code: int, type_: str, problema: dict[str, Any], detail_publico: str | None = None
) -> ArchivoRechazadoError | None:
    """Rechazo con el title y detail del problema, o None si alguno no es texto.

    Con `detail_publico`, ese texto reemplaza al detail original en la respuesta al cliente.
    """
    title, detail = problema.get("title"), problema.get("detail")
    if not (isinstance(title, str) and isinstance(detail, str)):
        return None
    return ArchivoRechazadoError(status_code, type_, title, detail_publico or detail)


def retry_after_valido(valor: str | None) -> str | None:
    """Devuelve el valor si es un Retry-After válido; si no, None."""
    if valor is None:
        return None
    if DELAY_SECONDS.fullmatch(valor):
        return valor
    if not IMF_FIXDATE.fullmatch(valor):
        return None
    try:
        parsedate_to_datetime(valor)  # descarta fechas imposibles, como el día 32
    except ValueError:
        return None
    return valor


def no_disponible(servicio: str, retry_after: str | None = None) -> ServicioNoDisponibleError:
    """Servicio no disponible; el Retry-After solo se conserva si es válido."""
    return ServicioNoDisponibleError(
        f"El servicio {servicio} no está disponible.", retry_after=retry_after_valido(retry_after)
    )


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
