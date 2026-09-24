"""Adaptadores falsos de Validator, Extractor y Persistence.

Se usan como implementación por defecto hasta que existan los clientes HTTP reales,
y en los tests. Registran las llamadas recibidas.
"""

from app.exceptions import ExtraccionFallidaError, PersistenciaFallidaError
from app.ports import ResultadoValidacion


class FakeValidator:
    def __init__(self, es_valido: bool = True, motivo: str | None = None):
        self._resultado = ResultadoValidacion(es_valido=es_valido, motivo=motivo)
        self.llamadas: list[tuple[str, bytes]] = []

    async def validar(self, nombre_archivo: str, contenido: bytes) -> ResultadoValidacion:
        self.llamadas.append((nombre_archivo, contenido))
        return self._resultado


class FakeExtractor:
    def __init__(self, texto: str = "texto extraído", falla: bool = False):
        self._texto = texto
        self._falla = falla
        self.llamadas: list[tuple[str, bytes]] = []

    async def extraer(self, nombre_archivo: str, contenido: bytes) -> str:
        self.llamadas.append((nombre_archivo, contenido))
        if self._falla:
            raise ExtraccionFallidaError("El Extractor no pudo procesar el archivo.")
        return self._texto


class FakePersistence:
    def __init__(self, falla: bool = False):
        self._falla = falla
        self.llamadas: list[tuple[str, str]] = []

    async def guardar(self, nombre_archivo: str, texto: str) -> None:
        self.llamadas.append((nombre_archivo, texto))
        if self._falla:
            raise PersistenciaFallidaError("Persistence no pudo guardar el texto.")
