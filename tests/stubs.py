"""Dobles de prueba que no forman parte de los fakes de la aplicación."""

from app.ports import ResultadoValidacion


class ServicioQueRegistra:
    """Cumple los tres ports con éxito y anota su nombre en un registro compartido."""

    def __init__(self, nombre: str, registro: list[str]):
        self._nombre = nombre
        self._registro = registro

    async def validar(self, *_):
        self._registro.append(self._nombre)
        return ResultadoValidacion(es_valido=True)

    async def extraer(self, *_):
        self._registro.append(self._nombre)
        return "texto"

    async def guardar(self, *_):
        self._registro.append(self._nombre)


class ServicioQueFalla:
    """Cumple los tres ports y lanza el error dado en cualquier operación."""

    def __init__(self, error: Exception):
        self._error = error

    async def validar(self, *_):
        raise self._error

    async def extraer(self, *_):
        raise self._error

    async def guardar(self, *_):
        raise self._error
