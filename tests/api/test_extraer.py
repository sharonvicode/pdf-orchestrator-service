import httpx
import pytest

from app.adapters.fakes import FakeExtractor, FakePersistence, FakeValidator
from app.adapters.http import ClientesHttp
from app.dependencies import get_clientes, get_orchestrator_service
from app.exceptions import ArchivoRechazadoError, ServicioNoDisponibleError, TiempoAgotadoError
from app.services.orchestrator import OrchestratorService
from tests.stubs import ServicioQueFalla

PROBLEM_JSON = "application/problem+json"
PDF = ("documento.pdf", b"%PDF-1.7 contenido", "application/pdf")


@pytest.fixture
def fakes():
    return FakeValidator(), FakeExtractor(texto="hola mundo"), FakePersistence()


@pytest.fixture
def usar_fakes(app, fakes):
    def _usar(validator=None, extractor=None, persistence=None):
        v, e, p = fakes
        servicio = OrchestratorService(validator=validator or v, extractor=extractor or e, persistence=persistence or p)
        app.dependency_overrides[get_orchestrator_service] = lambda: servicio

    _usar()
    yield _usar
    app.dependency_overrides.clear()


def test_extraer_exitoso_devuelve_200(client, usar_fakes):
    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 200


def test_extraer_exitoso_devuelve_exito_texto_y_nombre(client, usar_fakes):
    response = client.post("/extraer", files={"file": PDF})

    assert response.json() == {"exito": True, "texto": "hola mundo", "nombre_archivo": "documento.pdf"}


def test_extraer_delega_nombre_y_contenido_al_servicio(client, usar_fakes, fakes):
    validator, extractor, persistence = fakes

    client.post("/extraer", files={"file": PDF})

    assert validator.llamadas == [("documento.pdf", b"%PDF-1.7 contenido")]
    assert extractor.llamadas == [("documento.pdf", b"%PDF-1.7 contenido")]
    assert persistence.llamadas == [("documento.pdf", "hola mundo")]


def test_extraer_no_valida_el_pdf_por_su_cuenta(client, usar_fakes):
    # Si el Validator acepta, el endpoint no debe rechazar aunque el contenido no sea un PDF.
    response = client.post("/extraer", files={"file": ("notas.txt", b"no soy un pdf", "text/plain")})

    assert response.status_code == 200


def test_extraer_sin_archivo_devuelve_422_problem_json(client, usar_fakes):
    response = client.post("/extraer")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["errors"][0]["loc"] == ["body", "file"]


def test_extraer_rechazo_del_validator_conserva_status_type_y_detail(client, usar_fakes, fakes):
    rechazo = ArchivoRechazadoError(
        422, "/errors/invalid-pdf", "PDF inválido", "El PDF está dañado o no se puede leer."
    )
    usar_fakes(validator=FakeValidator(rechazo=rechazo))

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json() == {
        "type": "/errors/invalid-pdf",
        "title": "PDF inválido",
        "status": 422,
        "detail": "El PDF está dañado o no se puede leer.",
        "instance": "/extraer",
    }
    assert fakes[1].llamadas == []


def test_extraer_falla_extractor_devuelve_502_problem_json(client, usar_fakes, fakes):
    usar_fakes(extractor=FakeExtractor(falla=True))

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 502
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Falló la extracción de texto"
    assert fakes[2].llamadas == []


def test_extraer_falla_persistence_devuelve_502_problem_json(client, usar_fakes):
    usar_fakes(persistence=FakePersistence(falla=True))

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 502
    assert response.json()["title"] == "Falló la persistencia del texto"


@pytest.mark.parametrize(
    ("error", "status", "title"),
    [
        (ServicioNoDisponibleError, 503, "Servicio no disponible"),
        (TiempoAgotadoError, 504, "Tiempo de espera agotado"),
    ],
)
def test_extraer_error_de_comunicacion_devuelve_problem_json(client, usar_fakes, error, status, title):
    usar_fakes(extractor=ServicioQueFalla(error("El Extractor no respondió.")))

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == status
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == title
    assert body["status"] == status
    assert body["detail"] == "El Extractor no respondió."
    assert body["instance"] == "/extraer"
    assert "retry-after" not in response.headers


def test_extraer_servicio_no_disponible_propaga_retry_after(client, usar_fakes):
    usar_fakes(extractor=ServicioQueFalla(ServicioNoDisponibleError("El Extractor está saturado.", retry_after="1")))

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"


def test_extraer_rechazo_del_extractor_conserva_status_title_y_detail(client, usar_fakes, fakes):
    rechazo = ArchivoRechazadoError(422, "about:blank", "Contenido no procesable", "El PDF no tiene texto extraíble.")
    usar_fakes(extractor=ServicioQueFalla(rechazo))

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json() == {
        "type": "about:blank",
        "title": "Contenido no procesable",
        "status": 422,
        "detail": "El PDF no tiene texto extraíble.",
        "instance": "/extraer",
    }
    assert fakes[2].llamadas == []


def test_extraer_error_de_comunicacion_no_expone_la_causa_interna(client, usar_fakes):
    error = ServicioNoDisponibleError("El Validator no está disponible.")
    error.__cause__ = ConnectionError("connection refused: http://10.0.0.5:9000")
    usar_fakes(validator=ServicioQueFalla(error))

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 503
    assert "10.0.0.5" not in response.text
    assert "ConnectionError" not in response.text


@pytest.fixture
def servicios_http(app):
    """Usa la composición real con clientes HTTP simulados; responde según el path pedido."""
    recibidos: list[str] = []

    def _simular(**respuestas: httpx.Response):
        def manejar(request: httpx.Request) -> httpx.Response:
            recibidos.append(request.url.path)
            return respuestas[request.url.path.strip("/").replace("/", "_")]

        cliente = httpx.AsyncClient(base_url="http://servicio.example/", transport=httpx.MockTransport(manejar))
        app.dependency_overrides[get_clientes] = lambda: ClientesHttp(cliente, cliente, cliente)
        return recibidos

    yield _simular
    app.dependency_overrides.clear()


def test_extraer_sin_overrides_valida_con_el_validator_http(client, servicios_http):
    cuerpo = {
        "type": "/errors/unsupported-file-type",
        "title": "Tipo de archivo no soportado",
        "status": 415,
        "detail": "El archivo debe tener extensión .pdf.",
        "instance": "/validate",
    }
    recibidos = servicios_http(validate=httpx.Response(415, json=cuerpo, headers={"content-type": PROBLEM_JSON}))

    response = client.post("/extraer", files={"file": ("notas.txt", b"hola", "text/plain")})

    assert recibidos == ["/validate"]
    assert response.status_code == 415
    assert response.json()["type"] == "/errors/unsupported-file-type"
    assert response.json()["detail"] == "El archivo debe tener extensión .pdf."


def test_extraer_sin_overrides_extrae_con_el_extractor_http(client, servicios_http):
    exito = {"texto": "texto real", "metadatos": {"nombre_archivo": "documento.pdf", "cantidad_paginas": 3}}
    recibidos = servicios_http(validate=httpx.Response(200), api_v1_extraer=httpx.Response(200, json=exito))

    response = client.post("/extraer", files={"file": PDF})

    assert recibidos == ["/validate", "/api/v1/extraer"]
    assert response.status_code == 200
    assert response.json()["texto"] == "texto real"
