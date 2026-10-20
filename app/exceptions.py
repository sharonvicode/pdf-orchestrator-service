"""Errores de dominio del Orchestrator. Cada uno sabe cómo se traduce a HTTP."""


class OrchestratorError(Exception):
    status_code: int = 500
    type: str = "about:blank"
    title: str = "Error del Orchestrator"
    headers: dict[str, str] | None = None

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ArchivoRechazadoError(OrchestratorError):
    """Un servicio rechazó el archivo del cliente. Conserva el problema RFC 9457 original."""

    def __init__(self, status_code: int, type_: str, title: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.type = type_
        self.title = title


class ValidacionFallidaError(OrchestratorError):
    """El Validator respondió fuera de contrato."""

    status_code = 502
    title = "Falló la validación del archivo"


class ExtraccionFallidaError(OrchestratorError):
    status_code = 502
    title = "Falló la extracción de texto"


class PersistenciaFallidaError(OrchestratorError):
    status_code = 502
    title = "Falló la persistencia del texto"


class ServicioNoDisponibleError(OrchestratorError):
    """Un servicio downstream no respondió (conexión rechazada, caído, etc.)."""

    status_code = 503
    title = "Servicio no disponible"

    def __init__(self, detail: str, retry_after: str | None = None):
        super().__init__(detail)
        if retry_after:
            self.headers = {"Retry-After": retry_after}


class TiempoAgotadoError(OrchestratorError):
    """Un servicio downstream no respondió dentro del tiempo configurado."""

    status_code = 504
    title = "Tiempo de espera agotado"
