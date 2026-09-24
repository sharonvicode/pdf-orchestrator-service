"""Errores de dominio del Orchestrator. Cada uno sabe cómo se traduce a HTTP."""


class OrchestratorError(Exception):
    status_code: int = 500
    title: str = "Error del Orchestrator"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class PdfInvalidoError(OrchestratorError):
    status_code = 422
    title = "PDF inválido"


class ExtraccionFallidaError(OrchestratorError):
    status_code = 502
    title = "Falló la extracción de texto"


class PersistenciaFallidaError(OrchestratorError):
    status_code = 502
    title = "Falló la persistencia del texto"
