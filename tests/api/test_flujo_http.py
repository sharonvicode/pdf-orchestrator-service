"""Flujo completo con la composición real: los tres adapters HTTP sobre servicios simulados."""

import json

import httpx
import pytest

from app.adapters.http import ClientesHttp
from app.dependencies import get_clientes

PROBLEM_JSON = "application/problem+json"
PDF = ("documento.pdf", b"%PDF-1.7 contenido", "application/pdf")
ID = "6f1c2a9e-8f0e-4b8e-9a51-2b7c3d4e5f60"
VALIDATOR = "validator.example/validate"
EXTRACTOR = "extractor.example/api/v1/extraer"
PERSISTENCE = "persistence.example/extractions"
EXITO_EXTRACTOR = {
    "texto": "texto real",
    "metadatos": {
        "nombre_archivo": "documento.pdf",
        "tamanio_bytes": 18,
        "cantidad_paginas": 3,
        "tiempo_procesamiento_ms": 1.2,
    },
}
SERVICIOS_OK = {
    "/validate": httpx.Response(200),
    "/api/v1/extraer": httpx.Response(200, json=EXITO_EXTRACTOR),
    "/extractions": httpx.Response(201, json={"id": ID}),
}


def problema(status: int, **campos) -> httpx.Response:
    return httpx.Response(status, json={"status": status, **campos}, headers={"content-type": PROBLEM_JSON})


@pytest.fixture
def servicios(app):
    """Un cliente por servicio, como en producción; cada path responde lo indicado."""
    recibidos: list[httpx.Request] = []

    def _simular(cambios: dict[str, httpx.Response | Exception] | None = None) -> list[httpx.Request]:
        respuestas = {**SERVICIOS_OK, **(cambios or {})}

        def manejar(request: httpx.Request) -> httpx.Response:
            recibidos.append(request)
            respuesta = respuestas[request.url.path]
            if isinstance(respuesta, Exception):
                raise respuesta
            return respuesta

        transporte = httpx.MockTransport(manejar)
        clientes = ClientesHttp(
            *(
                httpx.AsyncClient(base_url=f"http://{nombre}.example/", transport=transporte)
                for nombre in ("validator", "extractor", "persistence")
            )
        )
        app.dependency_overrides[get_clientes] = lambda: clientes
        return recibidos

    yield _simular
    app.dependency_overrides.clear()


def llamadas(recibidos: list[httpx.Request]) -> list[str]:
    return [f"{request.url.host}{request.url.path}" for request in recibidos]


def test_flujo_exitoso_devuelve_exito_id_texto_y_nombre(client, servicios):
    servicios()

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 200
    assert response.json() == {"exito": True, "id": ID, "texto": "texto real", "nombre_archivo": "documento.pdf"}
    assert list(response.json()) == ["exito", "id", "texto", "nombre_archivo"]


def test_flujo_exitoso_llama_una_vez_a_cada_servicio_en_orden(client, servicios):
    recibidos = servicios()

    client.post("/extraer", files={"file": PDF})

    assert llamadas(recibidos) == [VALIDATOR, EXTRACTOR, PERSISTENCE]


def test_flujo_exitoso_persiste_texto_y_paginas_del_extractor(client, servicios):
    recibidos = servicios()

    client.post("/extraer", files={"file": PDF})

    assert json.loads(recibidos[2].content) == {"file_name": "documento.pdf", "text": "texto real", "page_count": 3}


def test_rechazo_del_validator_corta_el_flujo(client, servicios):
    rechazo = problema(
        415, type="/errors/unsupported-file-type", title="Tipo de archivo no soportado", detail="No es un PDF."
    )
    recibidos = servicios({"/validate": rechazo})

    response = client.post("/extraer", files={"file": ("notas.txt", b"hola", "text/plain")})

    assert response.status_code == 415
    assert response.json()["type"] == "/errors/unsupported-file-type"
    assert llamadas(recibidos) == [VALIDATOR]


def test_rechazo_422_del_extractor_no_persiste_y_usa_detail_fijo(client, servicios):
    rechazo = problema(422, type="about:blank", title="Contenido no procesable", detail="code=7: no objects found")
    recibidos = servicios({"/api/v1/extraer": rechazo})

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 422
    assert response.json()["title"] == "Contenido no procesable"
    assert "code=7" not in response.text
    assert llamadas(recibidos) == [VALIDATOR, EXTRACTOR]


@pytest.mark.parametrize(
    ("enviado", "publico"),
    [({"Retry-After": "5"}, "5"), ({"Retry-After": "pronto"}, None), ({}, None)],
    ids=["valido", "invalido", "ausente"],
)
def test_extractor_saturado_reenvia_solo_retry_after_valido(client, servicios, enviado, publico):
    recibidos = servicios({"/api/v1/extraer": httpx.Response(503, headers=enviado)})

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 503
    assert response.headers.get("retry-after") == publico
    assert llamadas(recibidos) == [VALIDATOR, EXTRACTOR]


@pytest.mark.parametrize(
    "respuesta",
    [
        pytest.param(httpx.Response(500, text="pymongo.errors.ServerSelectionTimeoutError"), id="500-texto-plano"),
        pytest.param(httpx.Response(422, json={"detail": [{"loc": ["body", "text"]}]}), id="422-json"),
        pytest.param(httpx.Response(201, json={"id": ""}), id="id-vacio"),
    ],
)
def test_falla_de_persistence_devuelve_502_sin_reintentar(client, servicios, respuesta):
    recibidos = servicios({"/extractions": respuesta})

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 502
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Falló la persistencia del texto"
    assert response.json()["detail"] == "Persistence respondió fuera del contrato esperado."
    assert "pymongo" not in response.text
    assert llamadas(recibidos) == [VALIDATOR, EXTRACTOR, PERSISTENCE]


@pytest.mark.parametrize(
    ("error", "status"),
    [(httpx.ReadTimeout("timed out"), 504), (httpx.ConnectError("connection refused"), 503)],
    ids=["timeout", "conexion-rechazada"],
)
def test_error_de_transporte_con_persistence_no_reintenta(client, servicios, error, status):
    recibidos = servicios({"/extractions": error})

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == status
    assert llamadas(recibidos) == [VALIDATOR, EXTRACTOR, PERSISTENCE]


def test_timeout_del_validator_corta_el_flujo(client, servicios):
    recibidos = servicios({"/validate": httpx.ReadTimeout("timed out")})

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 504
    assert llamadas(recibidos) == [VALIDATOR]
