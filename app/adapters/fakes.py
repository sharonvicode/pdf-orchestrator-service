"""Adaptadores falsos de Validator, Extractor y Persistence, para los tests.

No forman parte de la composición por defecto. Registran las llamadas recibidas.
"""

from app.exceptions import ArchivoRechazadoError, ExtraccionFallidaError, PersistenciaFallidaError
from app.ports import TextoExtraido


class FakeValidator:
    def __init__(self, rechazo: ArchivoRechazadoError | None = None):
        self._rechazo = rechazo
        self.llamadas: list[tuple[str, bytes]] = []

    async def validar(self, nombre_archivo: str, contenido: bytes) -> None:
        self.llamadas.append((nombre_archivo, contenido))
        if self._rechazo:
            raise self._rechazo


class FakeExtractor:
    def __init__(self, texto: str = "texto extraído", cantidad_paginas: int = 1, falla: bool = False):
        self._resultado = TextoExtraido(texto=texto, cantidad_paginas=cantidad_paginas)
        self._falla = falla
        self.llamadas: list[tuple[str, bytes]] = []

    async def extraer(self, nombre_archivo: str, contenido: bytes) -> TextoExtraido:
        self.llamadas.append((nombre_archivo, contenido))
        if self._falla:
            raise ExtraccionFallidaError("El Extractor no pudo procesar el archivo.")
        return self._resultado


class FakePersistence:
    def __init__(self, id: str = "id-falso", falla: bool = False):
        self._id = id
        self._falla = falla
        self.llamadas: list[tuple[str, str, int]] = []

    async def guardar(self, nombre_archivo: str, texto: str, cantidad_paginas: int) -> str:
        self.llamadas.append((nombre_archivo, texto, cantidad_paginas))
        if self._falla:
            raise PersistenciaFallidaError("Persistence no pudo guardar el texto.")
        return self._id
