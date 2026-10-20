"""Infraestructura HTTP común a los adapters: clientes compartidos y errores de transporte."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.exceptions import ServicioNoDisponibleError, TiempoAgotadoError


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


def no_disponible(servicio: str) -> ServicioNoDisponibleError:
    return ServicioNoDisponibleError(f"El servicio {servicio} no está disponible.")


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
