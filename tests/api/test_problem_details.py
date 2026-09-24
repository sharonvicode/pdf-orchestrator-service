"""RFC 9457: todos los errores se devuelven como application/problem+json."""

import pytest
from fastapi import Query
from fastapi.testclient import TestClient

PROBLEM_JSON = "application/problem+json"


@pytest.fixture
def app_con_rutas_de_prueba(app):
    @app.get("/_test/validacion")
    async def ruta_con_validacion(cantidad: int = Query(...)):
        return {"cantidad": cantidad}

    @app.get("/_test/explota")
    async def ruta_que_explota():
        raise RuntimeError("secreto interno: password=1234")

    return app


@pytest.fixture
def client_tolerante(app_con_rutas_de_prueba):
    return TestClient(app_con_rutas_de_prueba, raise_server_exceptions=False)


def test_ruta_inexistente_devuelve_problem_json(client):
    response = client.get("/no-existe")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


def test_ruta_inexistente_incluye_campos_rfc9457(client):
    body = client.get("/no-existe").json()

    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["instance"] == "/no-existe"
    assert "detail" in body


def test_metodo_no_permitido_devuelve_problem_json(client):
    response = client.post("/health/")

    assert response.status_code == 405
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["status"] == 405


def test_error_de_validacion_devuelve_422_problem_json(client_tolerante):
    response = client_tolerante.get("/_test/validacion", params={"cantidad": "no-es-numero"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


def test_error_de_validacion_incluye_lista_de_errores(client_tolerante):
    body = client_tolerante.get("/_test/validacion").json()

    assert body["status"] == 422
    assert body["title"] == "Unprocessable Content"
    assert body["instance"] == "/_test/validacion"
    assert isinstance(body["errors"], list)
    assert body["errors"][0]["loc"] == ["query", "cantidad"]
    assert "msg" in body["errors"][0]


def test_error_inesperado_devuelve_500_problem_json(client_tolerante):
    response = client_tolerante.get("/_test/explota")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["status"] == 500


def test_error_inesperado_no_expone_detalles_internos(client_tolerante):
    response = client_tolerante.get("/_test/explota")

    assert "secreto" not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
