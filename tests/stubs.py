"""Dobles de prueba que no forman parte de los fakes de la aplicación."""


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
