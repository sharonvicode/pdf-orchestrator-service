import httpx
import pytest

from app.adapters.extractor import HttpExtractor
from app.exceptions import (
    ArchivoRechazadoError,
    ExtraccionFallidaError,
    ServicioNoDisponibleError,
    TiempoAgotadoError,
)
from app.ports import TextoExtraido

pytestmark = pytest.mark.anyio

NOMBRE = "documento.pdf"
CONTENIDO = b"%PDF-1.7 contenido"
PROBLEM_JSON = "application/problem+json"
EXITO = {
    "texto": "Texto extraído",
    "metadatos": {
        "nombre_archivo": NOMBRE,
        "tamanio_bytes": 24531,
        "cantidad_paginas": 3,
        "tiempo_procesamiento_ms": 12.4,
    },
}


def extractor_que_responde(respuesta: httpx.Response | Exception, recibidos: list | None = None) -> HttpExtractor:
    def manejar(request: httpx.Request) -> httpx.Response:
        if recibidos is not None:
            recibidos.append(request)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    cliente = httpx.AsyncClient(base_url="http://extractor.example/", transport=httpx.MockTransport(manejar))
    return HttpExtractor(cliente)


def problema(status: int, title="Título", detail="Detalle", headers=None, **extensiones) -> httpx.Response:
    cuerpo = {"type": "about:blank", "title": title, "status": status, "detail": detail, "instance": "/api/v1/extraer"}
    return httpx.Response(
        status, json={**cuerpo, **extensiones}, headers={"content-type": PROBLEM_JSON, **(headers or {})}
    )


def exito_con(**cambios) -> httpx.Response:
    return httpx.Response(200, json={**EXITO, **cambios})


async def test_envia_el_archivo_por_post_api_v1_extraer_como_multipart_file_pdf():
    recibidos = []
    extractor = extractor_que_responde(httpx.Response(200, json=EXITO), recibidos)

    await extractor.extraer(NOMBRE, CONTENIDO)

    (request,) = recibidos
    assert request.method == "POST"
    assert request.url == "http://extractor.example/api/v1/extraer"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"; filename="documento.pdf"' in request.content
    assert b"Content-Type: application/pdf" in request.content
    assert CONTENIDO in request.content


async def test_200_devuelve_texto_y_cantidad_de_paginas():
    extractor = extractor_que_responde(httpx.Response(200, json=EXITO))

    assert await extractor.extraer(NOMBRE, CONTENIDO) == TextoExtraido(texto="Texto extraído", cantidad_paginas=3)


async def test_200_tolera_metadatos_adicionales():
    metadatos = {"cantidad_paginas": 0, "otro_campo": "x"}
    extractor = extractor_que_responde(exito_con(texto="", metadatos=metadatos))

    assert await extractor.extraer(NOMBRE, CONTENIDO) == TextoExtraido(texto="", cantidad_paginas=0)


@pytest.mark.parametrize(
    "respuesta",
    [
        pytest.param(httpx.Response(200, text="no json"), id="json-ilegible"),
        pytest.param(httpx.Response(200, json=[]), id="json-no-es-objeto"),
        pytest.param(httpx.Response(200, json={"metadatos": EXITO["metadatos"]}), id="sin-texto"),
        pytest.param(exito_con(texto=1), id="texto-no-es-texto"),
        pytest.param(httpx.Response(200, json={"texto": "x"}), id="sin-metadatos"),
        pytest.param(exito_con(metadatos={}), id="sin-cantidad-de-paginas"),
        pytest.param(exito_con(metadatos={"cantidad_paginas": "3"}), id="paginas-como-texto"),
        pytest.param(exito_con(metadatos={"cantidad_paginas": 2.5}), id="paginas-no-enteras"),
        pytest.param(exito_con(metadatos={"cantidad_paginas": True}), id="paginas-booleanas"),
        pytest.param(exito_con(metadatos={"cantidad_paginas": -1}), id="paginas-negativas"),
    ],
)
async def test_200_fuera_de_contrato_es_extraccion_fallida(respuesta):
    extractor = extractor_que_responde(respuesta)

    with pytest.raises(ExtraccionFallidaError):
        await extractor.extraer(NOMBRE, CONTENIDO)


async def test_413_conserva_status_title_y_detail_con_type_about_blank():
    detail = "El archivo pesa 20 bytes y supera el máximo de 10 bytes."
    extractor = extractor_que_responde(problema(413, title="Contenido demasiado grande", detail=detail))

    with pytest.raises(ArchivoRechazadoError) as error:
        await extractor.extraer(NOMBRE, CONTENIDO)

    assert error.value.status_code == 413
    assert error.value.type == "about:blank"
    assert error.value.title == "Contenido demasiado grande"
    assert error.value.detail == detail


@pytest.mark.parametrize(
    "detail_interno",
    ["No se pudo leer el archivo como PDF: code=7: no objects found", "El PDF no tiene texto extraíble."],
)
async def test_422_conserva_status_y_title_con_detail_fijo_sin_exponer_la_biblioteca(detail_interno):
    extractor = extractor_que_responde(problema(422, title="Contenido no procesable", detail=detail_interno))

    with pytest.raises(ArchivoRechazadoError) as error:
        await extractor.extraer(NOMBRE, CONTENIDO)

    assert error.value.status_code == 422
    assert error.value.type == "about:blank"
    assert error.value.title == "Contenido no procesable"
    assert error.value.detail == "No se pudo extraer el texto del PDF: está dañado o no tiene texto extraíble."


async def test_422_de_validacion_del_request_no_es_rechazo_del_archivo():
    # Mismo status que el rechazo contractual: solo la extensión `errors` los distingue.
    cuerpo = {"title": "Contenido no procesable", "detail": "La solicitud no es válida.", "errors": []}
    extractor = extractor_que_responde(problema(422, **cuerpo))

    with pytest.raises(ExtraccionFallidaError):
        await extractor.extraer(NOMBRE, CONTENIDO)


@pytest.mark.parametrize(
    "respuesta",
    [
        pytest.param(
            problema(422, detail="La solicitud no es válida.", errors=[{"loc": ["body", "file"]}]),
            id="422-de-request-mal-armado",
        ),
        pytest.param(problema(422, detail=None), id="sin-detail"),
        pytest.param(problema(413, title=["x"]), id="title-no-es-texto"),
        pytest.param(httpx.Response(422, json={"title": "t", "detail": "d"}), id="no-es-problem-json"),
        pytest.param(httpx.Response(413, text="no json", headers={"content-type": PROBLEM_JSON}), id="json-ilegible"),
        pytest.param(problema(500, detail="Ocurrió un error inesperado."), id="error-interno"),
        pytest.param(problema(404), id="ruta-inexistente"),
        pytest.param(problema(400), id="status-inesperado"),
        pytest.param(httpx.Response(201, json=EXITO), id="exito-con-status-inesperado"),
    ],
)
async def test_error_fuera_de_contrato_es_extraccion_fallida(respuesta):
    extractor = extractor_que_responde(respuesta)

    with pytest.raises(ExtraccionFallidaError) as error:
        await extractor.extraer(NOMBRE, CONTENIDO)

    assert error.value.detail == "El Extractor respondió fuera del contrato esperado."


@pytest.mark.parametrize("retry_after", ["1", "Wed, 21 Oct 2015 07:28:00 GMT"])
async def test_503_es_servicio_no_disponible_y_conserva_retry_after_valido(retry_after):
    extractor = extractor_que_responde(problema(503, headers={"Retry-After": retry_after}))

    with pytest.raises(ServicioNoDisponibleError) as error:
        await extractor.extraer(NOMBRE, CONTENIDO)

    assert error.value.headers == {"Retry-After": retry_after}


async def test_503_con_retry_after_invalido_no_agrega_encabezados():
    extractor = extractor_que_responde(problema(503, headers={"Retry-After": "pronto"}))

    with pytest.raises(ServicioNoDisponibleError) as error:
        await extractor.extraer(NOMBRE, CONTENIDO)

    assert error.value.headers is None


async def test_503_sin_retry_after_no_agrega_encabezados():
    extractor = extractor_que_responde(problema(503))

    with pytest.raises(ServicioNoDisponibleError) as error:
        await extractor.extraer(NOMBRE, CONTENIDO)

    assert error.value.headers is None


async def test_timeout_es_tiempo_agotado():
    extractor = extractor_que_responde(httpx.ReadTimeout("timed out"))

    with pytest.raises(TiempoAgotadoError):
        await extractor.extraer(NOMBRE, CONTENIDO)


async def test_conexion_rechazada_es_servicio_no_disponible():
    extractor = extractor_que_responde(httpx.ConnectError("connection refused"))

    with pytest.raises(ServicioNoDisponibleError) as error:
        await extractor.extraer(NOMBRE, CONTENIDO)

    assert error.value.headers is None
