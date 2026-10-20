"""Prueba de humo de punta a punta contra el stack de docker-compose.integration.yml.

Cliente → Traefik → Orchestrator → Validator → Extractor → Persistence → MongoDB, sin simular
ninguna llamada entre servicios. Se salta salvo que estén definidas:

- E2E_BASE_URL: URL pública de Traefik (por ejemplo http://localhost:8080).
- E2E_PDF: ruta a un PDF real con texto extraíble.
- E2E_PAGINAS: cantidad de páginas de ese PDF, para verificar el documento guardado.

Escribe exactamente UN documento en pdf_extractext_integracion.extractions
(test_flujo_completo_persiste_y_recupera). Detiene y vuelve a iniciar Persistence
(test_con_persistence_detenido_responde_503_sin_escribir) y pausa y reanuda el Validator
(test_con_validator_pausado_responde_504_al_vencer_el_timeout_sin_escribir). No borra datos ni volúmenes.

Si una corrida se corta sin llegar a la limpieza (proceso matado), se recupera con:
docker compose -f docker-compose.integration.yml start persistence
docker compose -f docker-compose.integration.yml unpause validator
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
PDF = Path(os.environ.get("E2E_PDF", ""))
PAGINAS = os.environ.get("E2E_PAGINAS", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (BASE_URL and PAGINAS and PDF.is_file()), reason="requiere E2E_BASE_URL, E2E_PDF y E2E_PAGINAS"
    ),
]

COMPOSE = ["docker", "compose", "-f", str(Path(__file__).parents[2] / "docker-compose.integration.yml")]
BASE_DE_DATOS = "pdf_extractext_integracion"
TIMEOUT_DEL_ORCHESTRATOR = 10  # HTTP_TIMEOUT_SECONDS en docker-compose.integration.yml
PROBLEM_JSON = "application/problem+json"
CONTRATOS = {
    "http://validator:8000": "/validate",
    "http://extractor:8000": "/api/v1/extraer",
    "http://persistence:8000": "/extractions",
}


def compose(*argumentos: str) -> str:
    resultado = subprocess.run([*COMPOSE, *argumentos], capture_output=True, text=True, encoding="utf-8", check=True, timeout=120)
    return resultado.stdout.strip()


def mongo(expresion: str) -> str:
    """Evalúa una expresión de solo lectura en la base de integración."""
    return compose("exec", "-T", "mongo", "mongosh", "--quiet", BASE_DE_DATOS, "--eval", expresion)


def contar_extracciones() -> int:
    return int(mongo("db.extractions.countDocuments()"))


def get_interno(url: str) -> tuple[int, str]:
    """GET desde la red interna (vía el contenedor del Orchestrator), sin publicar puertos."""
    script = (
        "import sys, urllib.request, urllib.error\n"
        "try:\n"
        "    r = urllib.request.urlopen(sys.argv[1], timeout=10); print(r.status); print(r.read().decode())\n"
        "except urllib.error.HTTPError as e:\n"
        "    print(e.code); print(e.read().decode())\n"
    )
    salida = compose("exec", "-T", "orchestrator", "python", "-c", script, url)
    status, _, cuerpo = salida.partition("\n")
    return int(status), cuerpo


def contenedores() -> list[dict]:
    """`docker compose ps --format json`: según la versión, un array o un objeto por línea."""
    salida = compose("ps", "--format", "json")
    if salida.startswith("["):
        return json.loads(salida)
    return [json.loads(linea) for linea in salida.splitlines() if linea.strip()]


def esperar_healthy(servicio: str, segundos: int = 90) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        estados = {c["Service"]: c.get("Health", "") for c in contenedores()}
        if estados.get(servicio) == "healthy":
            return
        time.sleep(2)
    pytest.fail(f"{servicio} no volvió a estar healthy en {segundos} s")


def subir(nombre: str, contenido: bytes, mime: str) -> httpx.Response:
    return httpx.post(f"{BASE_URL}/extraer", files={"file": (nombre, contenido, mime)}, timeout=60)


def test_health_a_traves_de_traefik():
    response = httpx.get(f"{BASE_URL}/health", timeout=10)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_solo_traefik_publica_puertos():
    publicados = {
        c["Service"]
        for c in contenedores()
        for publicacion in (c.get("Publishers") or [])
        if publicacion.get("PublishedPort")
    }

    assert publicados == {"traefik"}


@pytest.mark.parametrize(("servicio", "ruta"), CONTRATOS.items())
def test_cada_servicio_expone_la_ruta_post_que_consume_el_orchestrator(servicio, ruta):
    status, cuerpo = get_interno(f"{servicio}/openapi.json")

    assert status == 200
    assert "post" in json.loads(cuerpo)["paths"][ruta]


def test_persistence_consulta_mongodb_sin_escribir():
    # El healthcheck de Persistence solo prueba que el proceso responde. Este GET obliga a Persistence
    # a consultar MongoDB: 404 significa que la consulta se hizo y no encontró el id.
    antes = contar_extracciones()

    status, _ = get_interno("http://persistence:8000/extractions/smoke-inexistente")

    assert status == 404
    assert contar_extracciones() == antes


def test_flujo_completo_persiste_y_recupera():
    antes = contar_extracciones()

    response = subir(PDF.name, PDF.read_bytes(), "application/pdf")

    assert response.status_code == 200
    cuerpo = response.json()
    assert list(cuerpo) == ["exito", "id", "texto", "nombre_archivo"]
    assert cuerpo["exito"] is True
    assert cuerpo["nombre_archivo"] == PDF.name
    assert cuerpo["texto"].strip()
    uuid.UUID(cuerpo["id"])
    assert contar_extracciones() == antes + 1

    guardado = json.loads(
        mongo(
            f"const d = db.extractions.findOne({{_id: {json.dumps(cuerpo['id'])}}});"
            "EJSON.stringify(d ? {file_name: d.file_name, page_count: d.page_count, largo: d.text.length} : null)"
        )
    )
    # String.length de JavaScript cuenta unidades UTF-16.
    largo_utf16 = len(cuerpo["texto"].encode("utf-16-le")) // 2
    assert guardado == {"file_name": PDF.name, "page_count": int(PAGINAS), "largo": largo_utf16}

    status, recuperado = get_interno(f"http://persistence:8000/extractions/{cuerpo['id']}")
    assert status == 200
    assert json.loads(recuperado) == {
        "id": cuerpo["id"],
        "file_name": PDF.name,
        "text": cuerpo["texto"],
        "page_count": int(PAGINAS),
    }


def test_txt_rechazado_por_el_validator_sin_escribir():
    antes = contar_extracciones()

    response = subir("notas.txt", b"esto no es un pdf", "text/plain")

    assert response.status_code == 415
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["type"] == "/errors/unsupported-file-type"
    assert contar_extracciones() == antes


def test_con_persistence_detenido_responde_503_sin_escribir():
    antes = contar_extracciones()
    compose("stop", "persistence")
    try:
        response = subir(PDF.name, PDF.read_bytes(), "application/pdf")
    finally:
        compose("start", "persistence")
        esperar_healthy("persistence")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Servicio no disponible"
    assert contar_extracciones() == antes


def test_con_validator_pausado_responde_504_al_vencer_el_timeout_sin_escribir():
    # `pause` congela el proceso pero el kernel sigue aceptando la conexión: el Orchestrator espera
    # de verdad hasta que vence su timeout (a diferencia de `stop`, que falla al conectar). Se pausa
    # el Validator, el primer paso, para que el flujo nunca llegue a Persistence.
    antes = contar_extracciones()
    try:
        # Dentro del try: si `pause` falla después de congelar el contenedor, igual se reanuda.
        compose("pause", "validator")
        inicio = time.monotonic()
        response = subir(PDF.name, PDF.read_bytes(), "application/pdf")
        duracion = time.monotonic() - inicio
    finally:
        compose("unpause", "validator")
        esperar_healthy("validator")

    assert response.status_code == 504
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Tiempo de espera agotado"
    assert response.json()["detail"] == "El servicio Validator no respondió a tiempo."
    assert TIMEOUT_DEL_ORCHESTRATOR <= duracion < TIMEOUT_DEL_ORCHESTRATOR + 5
    assert contar_extracciones() == antes
