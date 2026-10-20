import json

import httpx
import pytest

from app.adapters.persistence import HttpPersistence
from app.exceptions import PersistenciaFallidaError, ServicioNoDisponibleError, TiempoAgotadoError

pytestmark = pytest.mark.anyio

NOMBRE = "documento.pdf"
TEXTO = "Texto extraído"
ID = "6f1c2a9e-8f0e-4b8e-9a51-2b7c3d4e5f60"
FUERA_DE_CONTRATO = "Persistence respondió fuera del contrato esperado."


def persistence_que_responde(respuesta: httpx.Response | Exception, recibidos: list | None = None) -> HttpPersistence:
    def manejar(request: httpx.Request) -> httpx.Response:
        if recibidos is not None:
            recibidos.append(request)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    cliente = httpx.AsyncClient(base_url="http://persistence.example/", transport=httpx.MockTransport(manejar))
    return HttpPersistence(cliente)


async def test_envia_post_extractions_con_json_del_contrato():
    recibidos = []
    persistence = persistence_que_responde(httpx.Response(201, json={"id": ID}), recibidos)

    await persistence.guardar(NOMBRE, TEXTO, 3)

    (request,) = recibidos
    assert request.method == "POST"
    assert request.url == "http://persistence.example/extractions"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"file_name": NOMBRE, "text": TEXTO, "page_count": 3}


async def test_201_devuelve_el_id_generado():
    persistence = persistence_que_responde(httpx.Response(201, json={"id": ID}))

    assert await persistence.guardar(NOMBRE, TEXTO, 3) == ID


@pytest.mark.parametrize(
    "respuesta",
    [
        pytest.param(httpx.Response(200, json={"id": ID}), id="200-en-lugar-de-201"),
        pytest.param(httpx.Response(202, json={"id": ID}), id="otro-2xx"),
        pytest.param(httpx.Response(201, text="no json"), id="201-json-ilegible"),
        pytest.param(httpx.Response(201, text=ID, headers={"content-type": "text/plain"}), id="201-texto-plano"),
        pytest.param(httpx.Response(201, json=[ID]), id="201-json-no-es-objeto"),
        pytest.param(httpx.Response(201, json={}), id="sin-id"),
        pytest.param(httpx.Response(201, json={"id": None}), id="id-nulo"),
        pytest.param(httpx.Response(201, json={"id": 123}), id="id-numerico"),
        pytest.param(httpx.Response(201, json={"id": ""}), id="id-vacio"),
        pytest.param(httpx.Response(201, json={"id": "   "}), id="id-en-blanco"),
        pytest.param(
            httpx.Response(422, json={"detail": [{"loc": ["body", "file_name"], "msg": "too short"}]}),
            id="422-json-de-fastapi",
        ),
        pytest.param(
            httpx.Response(500, text="Internal Server Error", headers={"content-type": "text/plain"}),
            id="500-texto-plano",
        ),
        pytest.param(httpx.Response(503, text="Service Unavailable"), id="503-no-esta-en-el-contrato"),
        pytest.param(httpx.Response(404, json={"detail": "Not Found"}), id="ruta-inexistente"),
    ],
)
async def test_respuesta_fuera_de_contrato_es_persistencia_fallida_con_mensaje_fijo(respuesta):
    persistence = persistence_que_responde(respuesta)

    with pytest.raises(PersistenciaFallidaError) as error:
        await persistence.guardar(NOMBRE, TEXTO, 3)

    assert error.value.detail == FUERA_DE_CONTRATO


async def test_el_cuerpo_de_error_de_persistence_no_llega_al_mensaje_publico():
    respuesta = httpx.Response(500, text="pymongo.errors.ServerSelectionTimeoutError: mongo:27017")
    persistence = persistence_que_responde(respuesta)

    with pytest.raises(PersistenciaFallidaError) as error:
        await persistence.guardar(NOMBRE, TEXTO, 3)

    assert "mongo" not in error.value.detail


async def test_no_reintenta_ante_un_error():
    recibidos = []
    persistence = persistence_que_responde(httpx.Response(500), recibidos)

    with pytest.raises(PersistenciaFallidaError):
        await persistence.guardar(NOMBRE, TEXTO, 3)

    assert len(recibidos) == 1


async def test_timeout_es_tiempo_agotado_sin_reintentar():
    recibidos = []
    persistence = persistence_que_responde(httpx.ReadTimeout("timed out"), recibidos)

    with pytest.raises(TiempoAgotadoError):
        await persistence.guardar(NOMBRE, TEXTO, 3)

    assert len(recibidos) == 1


async def test_conexion_rechazada_es_servicio_no_disponible():
    persistence = persistence_que_responde(httpx.ConnectError("connection refused"))

    with pytest.raises(ServicioNoDisponibleError):
        await persistence.guardar(NOMBRE, TEXTO, 3)
