import pytest

from app.adapters.fakes import FakeExtractor, FakePersistence, FakeValidator
from app.dependencies import get_orchestrator_service
from app.services.orchestrator import OrchestratorService

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


def test_extraer_pdf_invalido_devuelve_422_problem_json(client, usar_fakes, fakes):
    usar_fakes(validator=FakeValidator(es_valido=False, motivo="No es un PDF"))

    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["title"] == "PDF inválido"
    assert body["detail"] == "No es un PDF"
    assert body["instance"] == "/extraer"
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


def test_extraer_sin_overrides_usa_fakes_por_defecto(client):
    response = client.post("/extraer", files={"file": PDF})

    assert response.status_code == 200
    assert response.json()["exito"] is True
