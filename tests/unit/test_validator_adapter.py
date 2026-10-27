import httpx
import pytest

from app.adapters.validator import HttpValidator
from app.exceptions import (
    ArchivoRechazadoError,
    ServicioNoDisponibleError,
    TiempoAgotadoError,
    ValidacionFallidaError,
)

pytestmark = pytest.mark.anyio

NOMBRE = "documento.pdf"
CONTENIDO = b"%PDF-1.7 contenido"
PROBLEM_JSON = "application/problem+json"


def validator_que_responde(respuesta: httpx.Response | Exception, recibidos: list | None = None) -> HttpValidator:
    def manejar(request: httpx.Request) -> httpx.Response:
        if recibidos is not None:
            recibidos.append(request)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    cliente = httpx.AsyncClient(base_url="http://validator.example/", transport=httpx.MockTransport(manejar))
    return HttpValidator(cliente)


def problema(status: int, type_="/errors/invalid-pdf", title="Título", detail="Detalle", **cambios) -> httpx.Response:
    cuerpo = {"type": type_, "title": title, "status": status, "detail": detail, "instance": "/validate", **cambios}
    return httpx.Response(status, json=cuerpo, headers={"content-type": PROBLEM_JSON})


async def test_envia_el_archivo_por_post_validate_como_multipart_file_pdf():
    recibidos = []
    validator = validator_que_responde(httpx.Response(200), recibidos)

    await validator.validar(NOMBRE, CONTENIDO)

    (request,) = recibidos
    assert request.method == "POST"
    assert request.url == "http://validator.example/validate"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"; filename="documento.pdf"' in request.content
    assert b"Content-Type: application/pdf" in request.content
    assert CONTENIDO in request.content


async def test_200_acepta_el_archivo_sin_depender_del_cuerpo():
    validator = validator_que_responde(httpx.Response(200))

    assert await validator.validar(NOMBRE, CONTENIDO) is None


@pytest.mark.parametrize(
    ("status", "type_"),
    [
        (400, "/errors/empty-file"),
        (413, "/errors/file-too-large"),
        (415, "/errors/unsupported-file-type"),
        (422, "/errors/invalid-pdf"),
    ],
)
async def test_rechazo_del_archivo_conserva_status_type_title_y_detail(status, type_):
    validator = validator_que_responde(problema(status, type_, title="Título original", detail="Detalle original"))

    with pytest.raises(ArchivoRechazadoError) as error:
        await validator.validar(NOMBRE, CONTENIDO)

    assert error.value.status_code == status
    assert error.value.type == type_
    assert error.value.title == "Título original"
    assert error.value.detail == "Detalle original"


@pytest.mark.parametrize(
    "respuesta",
    [
        pytest.param(problema(400, "/errors/bad-request"), id="bad-request-es-error-del-orchestrator"),
        pytest.param(problema(422, "/errors/desconocido"), id="type-desconocido"),
        pytest.param(problema(415, "/errors/unsupported-file-type", detail=None), id="sin-detail"),
        pytest.param(problema(413, "/errors/file-too-large", title=1), id="title-no-es-texto"),
        pytest.param(problema(400, ["/errors/empty-file"]), id="type-no-es-texto"),
        pytest.param(httpx.Response(415, json={"type": "/errors/unsupported-file-type"}), id="no-es-problem-json"),
        pytest.param(httpx.Response(422, text="no json", headers={"content-type": PROBLEM_JSON}), id="json-ilegible"),
        pytest.param(httpx.Response(400, json=[], headers={"content-type": PROBLEM_JSON}), id="json-no-es-objeto"),
        pytest.param(problema(500, "/errors/internal"), id="error-interno"),
        pytest.param(problema(404, "/errors/not-found"), id="ruta-inexistente"),
        pytest.param(httpx.Response(201), id="status-inesperado"),
    ],
)
async def test_respuesta_fuera_de_contrato_es_error_de_integracion(respuesta):
    validator = validator_que_responde(respuesta)

    with pytest.raises(ValidacionFallidaError):
        await validator.validar(NOMBRE, CONTENIDO)


async def test_503_es_servicio_no_disponible():
    validator = validator_que_responde(problema(503, "about:blank"))

    with pytest.raises(ServicioNoDisponibleError):
        await validator.validar(NOMBRE, CONTENIDO)


async def test_timeout_es_tiempo_agotado():
    validator = validator_que_responde(httpx.ReadTimeout("timed out"))

    with pytest.raises(TiempoAgotadoError):
        await validator.validar(NOMBRE, CONTENIDO)


async def test_conexion_rechazada_es_servicio_no_disponible():
    validator = validator_que_responde(httpx.ConnectError("connection refused"))

    with pytest.raises(ServicioNoDisponibleError):
        await validator.validar(NOMBRE, CONTENIDO)
