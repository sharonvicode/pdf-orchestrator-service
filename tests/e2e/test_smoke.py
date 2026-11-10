"""Prueba de humo de punta a punta contra el stack de docker-compose.integration.yml.

Cliente → Traefik → Orchestrator → Validator → Extractor → Persistence → MongoDB, sin simular
ninguna llamada entre servicios. Se salta salvo que estén definidas:

- E2E_BASE_URL: URL pública de Traefik (por ejemplo http://localhost:8080).
- E2E_PDF: ruta a un PDF real con texto extraíble.
- E2E_PAGINAS: cantidad de páginas de ese PDF, para verificar el documento guardado.

Escribe exactamente UN documento en pdf_extractext_integracion.extractions
(test_flujo_completo_persiste_y_recupera). Detiene y vuelve a iniciar Persistence
(test_con_persistence_detenido_traefik_responde_404_y_el_orchestrator_502_sin_escribir) y pausa y
reanuda el Validator
(test_con_validator_pausado_traefik_corta_a_los_8s_y_el_orchestrator_responde_502_sin_escribir). Pausa y reanuda, de a
uno, Validator, Extractor y Persistence para abrir y recuperar su circuit breaker
(test_circuit_breaker_se_abre_rechaza_sin_escribir_y_se_recupera). No borra datos ni volúmenes.

Si una corrida se corta sin llegar a la limpieza (proceso matado), se recupera con:
docker compose -f docker-compose.integration.yml start persistence
docker compose -f docker-compose.integration.yml unpause validator extractor persistence
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
# Circuit breaker (docker-compose.integration.yml): entrypoint interno de Traefik, timeout del
# serversTransport interno y duraciones del middleware.
TRAEFIK_INTERNO = "http://traefik:8081"
SERVICIOS = ("validator", "extractor", "persistence")
TIMEOUT_DE_TRAEFIK = 8
VENTANA_ABIERTO = 10  # fallbackDuration
RECUPERACION = 10  # recoveryDuration


def compose(*argumentos: str) -> str:
    resultado = subprocess.run(
        [*COMPOSE, *argumentos], capture_output=True, text=True, encoding="utf-8", check=True, timeout=120
    )
    return resultado.stdout.strip()


def mongo(expresion: str) -> str:
    """Evalúa una expresión de solo lectura en la base de integración."""
    return compose("exec", "-T", "mongo", "mongosh", "--quiet", BASE_DE_DATOS, "--eval", expresion)


def contar_extracciones() -> int:
    return int(mongo("db.extractions.countDocuments()"))


def sondear(url: str) -> tuple[int, float, str]:
    """GET desde la red interna (vía el contenedor del Orchestrator), sin publicar puertos.

    Devuelve status, segundos medidos dentro del contenedor (sin el costo de `docker exec`) y cuerpo.
    Si no hay respuesta HTTP (conexión rechazada, timeout del cliente), el status es 0.
    """
    script = (
        "import sys, time, urllib.request, urllib.error\n"
        "inicio = time.monotonic()\n"
        "try:\n"
        "    r = urllib.request.urlopen(sys.argv[1], timeout=15); status, cuerpo = r.status, r.read().decode()\n"
        "except urllib.error.HTTPError as e:\n"
        "    status, cuerpo = e.code, e.read().decode()\n"
        "except OSError as e:\n"
        "    status, cuerpo = 0, repr(e)\n"
        "print(status); print(time.monotonic() - inicio); print(cuerpo)\n"
    )
    salida = compose("exec", "-T", "orchestrator", "python", "-c", script, url)
    status, segundos, *cuerpo = salida.split("\n", 2)  # compose() recorta el cuerpo vacío
    return int(status), float(segundos), "".join(cuerpo)


def get_interno(url: str) -> tuple[int, str]:
    status, _, cuerpo = sondear(url)
    return status, cuerpo


def esperar_status(url: str, esperado: int, segundos: float) -> float:
    """Repite el GET interno hasta recibir `esperado`; devuelve cuánto tardó (monotonic del host)."""
    inicio = time.monotonic()
    while (transcurrido := time.monotonic() - inicio) < segundos:
        if sondear(url)[0] == esperado:
            return transcurrido
        time.sleep(0.5)
    pytest.fail(f"{url} no respondió {esperado} en {segundos} s")


def esperar_alcanzable(servicio: str) -> None:
    """Espera a que el Orchestrator vuelva a alcanzar el servicio por la URL que usa.

    Si esa URL pasa por Traefik, incluye esperar a que el circuit breaker se cierre.
    """
    url = compose("exec", "-T", "orchestrator", "printenv", f"{servicio.upper()}_URL")
    esperar_status(f"{url.rstrip('/')}/openapi.json", 200, VENTANA_ABIERTO + RECUPERACION + 15)


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


def test_con_persistence_detenido_traefik_responde_404_y_el_orchestrator_502_sin_escribir():
    # Con el contenedor detenido, el check TCP de Consul falla (cada 1 s) y Traefik deja de
    # descubrirlo y borra su ruta: responde 404 sin pasar por el circuit breaker. El adapter de
    # Persistence trata toda respuesta distinta de 201 como fuera de contrato (502).
    antes = contar_extracciones()
    compose("stop", "persistence")
    try:
        esperar_status(f"{TRAEFIK_INTERNO}/persistence/openapi.json", 404, 5)
        status_traefik, _ = get_interno(f"{TRAEFIK_INTERNO}/persistence/openapi.json")
        response = subir(PDF.name, PDF.read_bytes(), "application/pdf")
    finally:
        compose("start", "persistence")
        esperar_healthy("persistence")
        esperar_alcanzable("persistence")

    assert status_traefik == 404
    assert response.status_code == 502
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Falló la persistencia del texto"
    assert response.json()["detail"] == "Persistence respondió fuera del contrato esperado."
    assert contar_extracciones() == antes


def test_con_validator_pausado_traefik_corta_a_los_8s_y_el_orchestrator_responde_502_sin_escribir():
    # `pause` congela el proceso pero el kernel sigue aceptando la conexión: la llamada espera de verdad
    # (a diferencia de `stop`). Traefik corta primero, al vencer su responseHeaderTimeout, con un 504
    # que cuenta para el circuit breaker. El adapter del Validator no interpreta ese 504 como timeout:
    # lo trata como respuesta fuera de contrato (502). La duración prueba que cortó Traefik y no el
    # Orchestrator. Se pausa el Validator, el primer paso, para que el flujo nunca llegue a Persistence.
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
        # Este fallo abre el circuito del Validator: se espera a que se cierre para no contaminar
        # las pruebas siguientes.
        esperar_alcanzable("validator")

    assert response.status_code == 502
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Falló la validación del archivo"
    assert response.json()["detail"] == "El Validator respondió fuera del contrato esperado."
    assert TIMEOUT_DE_TRAEFIK <= duracion < TIMEOUT_DEL_ORCHESTRATOR
    assert contar_extracciones() == antes


def test_traefik_publica_solo_el_entrypoint_web():
    # El entrypoint interno (8081) solo se usa desde la red de Docker: no se publica en el host.
    puertos = {
        publicacion.get("TargetPort")
        for c in contenedores()
        if c["Service"] == "traefik"
        for publicacion in (c.get("Publishers") or [])
        if publicacion.get("PublishedPort")
    }

    assert puertos == {80}


@pytest.mark.parametrize(("servicio", "ruta"), zip(SERVICIOS, CONTRATOS.values()))
def test_cada_servicio_responde_a_traves_del_entrypoint_interno(servicio, ruta):
    # Traefik descubre cada servicio desde Consul y quita el prefijo /<servicio>.
    status, cuerpo = get_interno(f"{TRAEFIK_INTERNO}/{servicio}/openapi.json")

    assert status == 200
    assert "post" in json.loads(cuerpo)["paths"][ruta]


def test_consul_registra_solo_los_tres_servicios_saludables():
    status_servicios, cuerpo_servicios = get_interno("http://consul:8500/v1/agent/services")
    status_checks, cuerpo_checks = get_interno("http://consul:8500/v1/agent/checks")

    assert status_servicios == 200
    assert status_checks == 200
    servicios = json.loads(cuerpo_servicios)
    checks = json.loads(cuerpo_checks)
    assert set(servicios) == set(SERVICIOS)
    assert {s["Address"]: s["Port"] for s in servicios.values()} == {s: 8000 for s in SERVICIOS}
    assert {c["ServiceName"]: c["Status"] for c in checks.values()} == {s: "passing" for s in SERVICIOS}


# Respuesta del Orchestrator al cliente con el circuito del servicio abierto (Traefik responde 503).
# Validator y Extractor ya traducen un 503 a "Servicio no disponible". El adapter de Persistence
# trata toda respuesta distinta de 201 como fuera de contrato (502): traducir el 503 de Traefik
# requeriría modificar el código Python, que no está autorizado.
RECHAZO_CON_CIRCUITO_ABIERTO = {
    "validator": (503, "Servicio no disponible", "El servicio Validator no está disponible."),
    "extractor": (503, "Servicio no disponible", "El servicio Extractor no está disponible."),
    "persistence": (502, "Falló la persistencia del texto", "Persistence respondió fuera del contrato esperado."),
}


@pytest.mark.parametrize("servicio", SERVICIOS)
def test_circuit_breaker_se_abre_rechaza_sin_escribir_y_se_recupera(servicio):
    # `pause` (no `stop`): con el contenedor detenido Traefik borra la ruta y responde 404, sin
    # pasar por el circuit breaker. Pausado, el kernel acepta la conexión y Traefik corta con 504
    # al vencer su timeout: un error de red que abre el circuito.
    # El circuito se abre con un GET de solo lectura: un POST a Persistence quedaría encolado en el
    # contenedor pausado y podría escribir al reanudarlo, aunque el cliente ya recibió el error.
    ruta = f"{TRAEFIK_INTERNO}/{servicio}/openapi.json"
    otros = [f"{TRAEFIK_INTERNO}/{otro}/openapi.json" for otro in SERVICIOS if otro != servicio]
    antes = contar_extracciones()
    try:
        compose("pause", servicio)
        status_apertura, segundos_apertura, _ = sondear(ruta)
        abierto_desde = time.monotonic()
        status_abierto, segundos_abierto, _ = sondear(ruta)
        status_otros = [sondear(url)[0] for url in otros]
        inicio = time.monotonic()
        response = subir(PDF.name, PDF.read_bytes(), "application/pdf")
        duracion = time.monotonic() - inicio
    finally:
        compose("unpause", servicio)
        esperar_healthy(servicio)
    recuperado_a_los = time.monotonic() - abierto_desde + esperar_status(ruta, 200, VENTANA_ABIERTO + RECUPERACION + 15)

    # Traefik detecta el fallo antes que el Orchestrator y lo cuenta.
    assert status_apertura == 504
    assert TIMEOUT_DE_TRAEFIK <= segundos_apertura < TIMEOUT_DEL_ORCHESTRATOR
    # Superado el umbral, el circuito abierto responde 503 sin esperar al servicio.
    assert status_abierto == 503
    assert segundos_abierto < 1
    # Los circuitos de los otros dos servicios siguen cerrados.
    assert status_otros == [200, 200]
    # El cliente recibe el rechazo sin esperar ningún timeout y no se escribe nada.
    status, title, detail = RECHAZO_CON_CIRCUITO_ABIERTO[servicio]
    assert response.status_code == status
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert (response.json()["title"], response.json()["detail"]) == (title, detail)
    assert duracion < TIMEOUT_DE_TRAEFIK
    assert contar_extracciones() == antes
    # No deja pasar tráfico durante la ventana abierta y se cierra al terminar la recuperación.
    # Margen de 1 s: abierto_desde se toma en el host, después del `docker exec` que abrió el circuito.
    assert VENTANA_ABIERTO - 1 <= recuperado_a_los <= VENTANA_ABIERTO + RECUPERACION + 15
