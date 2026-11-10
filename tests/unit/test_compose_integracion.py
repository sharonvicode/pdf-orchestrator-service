"""Contrato de docker-compose.integration.yml: Consul, Traefik y circuit breakers.

El Orchestrator llama a Validator, Extractor y Persistence a través de un entrypoint interno
de Traefik. Los tres servicios se registran en Consul y Traefik los descubre mediante su
provider consulCatalog, aplicando a cada uno su propio circuit breaker. Estos tests leen el
compose y sus configs inline igual que Consul y Traefik, sin levantar contenedores.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

COMPOSE_FILE = Path(__file__).parents[2] / "docker-compose.integration.yml"
SERVICIOS = ("validator", "extractor", "persistence")
ENTRYPOINT_INTERNO = "interno"
SERVERS_TRANSPORT = "interno"
RED_INTERNA = "pdf-integracion-backend"
ARCHIVO_CONSUL = "/consul/config/servicios.json"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def comando_traefik(compose) -> list[str]:
    return compose["services"]["traefik"]["command"]


@pytest.fixture(scope="module")
def entorno_orchestrator(compose) -> dict[str, str]:
    return compose["services"]["orchestrator"]["environment"]


def opcion(comando: list[str], nombre: str) -> str | None:
    """Valor de `--nombre=valor` en el comando de Traefik, o None si no está."""
    prefijo = f"--{nombre}="
    return next((arg.removeprefix(prefijo) for arg in comando if arg.startswith(prefijo)), None)


def servicios_consul(compose: dict) -> dict[str, dict]:
    """Servicios registrados en el config inline de Consul, indexados por nombre."""
    montaje = next(c for c in compose["services"]["consul"].get("configs", []) if c["target"] == ARCHIVO_CONSUL)
    contenido = json.loads(compose["configs"][montaje["source"]]["content"])
    return {s["name"]: s for s in contenido["services"]}


def labels(compose: dict, servicio: str) -> dict[str, str]:
    """Etiquetas de Traefik: de Consul (tags) para los tres servicios internos y de Docker para el resto."""
    if servicio in SERVICIOS:
        entradas = servicios_consul(compose)[servicio].get("tags", [])
    else:
        entradas = compose["services"][servicio].get("labels", [])
    return dict(etiqueta.split("=", 1) for etiqueta in entradas)


def segundos(duracion: str) -> float:
    """Duración de Traefik en segundos (solo se usan valores enteros en `s`)."""
    coincidencia = re.fullmatch(r"([0-9]+)s", duracion)
    assert coincidencia, f"duración no soportada por el test: {duracion!r}"
    return float(coincidencia.group(1))


def puerto_interno(comando: list[str]) -> int:
    direccion = opcion(comando, f"entrypoints.{ENTRYPOINT_INTERNO}.address")
    assert direccion is not None, "falta el entrypoint interno de Traefik"
    return int(direccion.rsplit(":", 1)[1])


def configuracion_dinamica(compose: dict, comando: list[str]) -> dict:
    """Contenido del archivo que lee el file provider de Traefik (un config inline del compose)."""
    archivo = opcion(comando, "providers.file.filename")
    assert archivo is not None, "Traefik no tiene file provider"
    montaje = next(c for c in compose["services"]["traefik"].get("configs", []) if c["target"] == archivo)
    return yaml.safe_load(compose["configs"][montaje["source"]]["content"])


class TestEntrypointInterno:
    def test_traefik_escucha_en_un_entrypoint_interno(self, comando_traefik):
        assert puerto_interno(comando_traefik) > 0

    def test_el_entrypoint_interno_no_se_publica_en_el_host(self, compose, comando_traefik):
        puerto = puerto_interno(comando_traefik)
        publicados = compose["services"]["traefik"].get("ports", [])

        assert not [p for p in publicados if str(p).rsplit(":", 1)[-1] == str(puerto)]

    def test_traefik_alcanza_la_red_interna(self, compose):
        # Validator, Extractor y Persistence solo están en la red backend.
        assert "backend" in compose["services"]["traefik"]["networks"]

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_los_servicios_internos_no_publican_puertos(self, compose, servicio):
        assert "ports" not in compose["services"][servicio]

    def test_el_router_publico_del_orchestrator_no_usa_el_entrypoint_interno(self, compose):
        assert labels(compose, "orchestrator")["traefik.http.routers.orchestrator.entrypoints"] == "web"


class TestUrlsDelOrchestrator:
    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_cada_url_pasa_por_el_entrypoint_interno_de_traefik(self, entorno_orchestrator, comando_traefik, servicio):
        url = urlsplit(entorno_orchestrator[f"{servicio.upper()}_URL"])

        assert url.scheme == "http"
        assert url.hostname == "traefik"  # nombre del servicio en Docker, nunca localhost
        assert url.port == puerto_interno(comando_traefik)
        assert url.path == f"/{servicio}"

    def test_el_timeout_del_orchestrator_no_cambia(self, entorno_orchestrator):
        assert entorno_orchestrator["HTTP_TIMEOUT_SECONDS"] == "10"


class TestRouterInterno:
    """Lo que pide el Orchestrator (prefijo de su URL) es lo que Traefik enruta a cada servicio."""

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_el_router_solo_escucha_en_el_entrypoint_interno(self, compose, servicio):
        etiquetas = labels(compose, servicio)

        assert etiquetas["traefik.enable"] == "true"
        assert etiquetas[f"traefik.http.routers.{servicio}.entrypoints"] == ENTRYPOINT_INTERNO

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_el_router_enruta_el_prefijo_de_la_url_del_orchestrator(self, compose, entorno_orchestrator, servicio):
        prefijo = urlsplit(entorno_orchestrator[f"{servicio.upper()}_URL"]).path
        etiquetas = labels(compose, servicio)

        assert etiquetas[f"traefik.http.routers.{servicio}.rule"] == f"PathPrefix(`{prefijo}`)"

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_el_prefijo_se_quita_antes_de_llegar_al_servicio(self, compose, servicio):
        # El servicio sigue recibiendo sus rutas originales (/validate, /api/v1/extraer, /extractions).
        etiquetas = labels(compose, servicio)
        middlewares = etiquetas[f"traefik.http.routers.{servicio}.middlewares"].split(",")
        prefijos = {
            etiquetas[f"traefik.http.middlewares.{m}.stripprefix.prefixes"]
            for m in middlewares
            if f"traefik.http.middlewares.{m}.stripprefix.prefixes" in etiquetas
        }

        assert prefijos == {f"/{servicio}"}

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_el_servicio_se_alcanza_por_la_red_interna_en_el_puerto_8000(self, compose, servicio):
        etiquetas = labels(compose, servicio)
        registro = servicios_consul(compose)[servicio]

        assert "backend" in compose["services"][servicio]["networks"]
        assert compose["networks"]["backend"]["name"] == RED_INTERNA
        assert registro["address"] == servicio
        assert registro["port"] == 8000
        assert etiquetas[f"traefik.http.services.{servicio}.loadbalancer.server.port"] == "8000"


class TestConsul:
    def test_consul_vive_solo_en_la_red_interna_sin_publicar_puertos(self, compose):
        consul = compose["services"]["consul"]

        assert consul["networks"] == ["backend"]
        assert "ports" not in consul

    def test_solo_se_registran_validator_extractor_y_persistence(self, compose):
        # MongoDB sigue siendo responsabilidad exclusiva de Persistence y no se registra en Consul.
        assert set(servicios_consul(compose)) == set(SERVICIOS)

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_cada_servicio_tiene_check_tcp_en_consul(self, compose, servicio):
        check = servicios_consul(compose)[servicio]["check"]

        assert check["tcp"] == f"{servicio}:8000"

    def test_traefik_descubre_los_servicios_mediante_consul_catalog(self, comando_traefik):
        assert opcion(comando_traefik, "providers.consulcatalog") == "true"
        assert opcion(comando_traefik, "providers.consulcatalog.endpoint.address") == "consul:8500"
        assert opcion(comando_traefik, "providers.consulcatalog.exposedbydefault") == "false"

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_los_servicios_internos_no_se_descubren_por_docker_labels(self, compose, servicio):
        assert "labels" not in compose["services"][servicio]


class TestCircuitBreaker:
    @staticmethod
    def circuit_breaker(compose: dict, servicio: str) -> tuple[str, dict[str, str]]:
        """Nombre y opciones del único middleware circuitbreaker del router del servicio."""
        etiquetas = labels(compose, servicio)
        middlewares = etiquetas.get(f"traefik.http.routers.{servicio}.middlewares", "").split(",")
        encontrados = {
            m: {
                clave.rsplit(".", 1)[1]: valor
                for clave, valor in etiquetas.items()
                if clave.startswith(f"traefik.http.middlewares.{m}.circuitbreaker.")
            }
            for m in middlewares
        }
        encontrados = {m: opciones for m, opciones in encontrados.items() if opciones}
        assert len(encontrados) == 1, f"{servicio} debe tener exactamente un circuit breaker en su router"
        return next(iter(encontrados.items()))

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_se_abre_solo_por_errores_de_red(self, compose, servicio):
        # NetworkErrorRatio cuenta solo 502/504 (servicio inalcanzable o timeout de Traefik).
        # El 503 del backpressure del Extractor no es un error de red: no abre el circuito.
        _, opciones = self.circuit_breaker(compose, servicio)

        assert opciones["expression"] == "NetworkErrorRatio() > 0.50"

    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_queda_abierto_10s_y_se_recupera_en_10s(self, compose, servicio):
        _, opciones = self.circuit_breaker(compose, servicio)

        assert opciones["fallbackDuration"] == "10s"
        assert opciones["recoveryDuration"] == "10s"

    def test_cada_servicio_tiene_su_propio_circuit_breaker(self, compose):
        nombres = {self.circuit_breaker(compose, servicio)[0] for servicio in SERVICIOS}

        assert nombres == {f"{servicio}-circuit-breaker" for servicio in SERVICIOS}


class TestTimeoutDeTraefik:
    @pytest.mark.parametrize("servicio", SERVICIOS)
    def test_cada_servicio_usa_el_servers_transport_interno(self, compose, servicio):
        etiquetas = labels(compose, servicio)

        assert etiquetas[f"traefik.http.services.{servicio}.loadbalancer.serverstransport"] == (
            f"{SERVERS_TRANSPORT}@file"
        )

    def test_traefik_corta_antes_que_el_orchestrator(self, compose, comando_traefik, entorno_orchestrator):
        # Traefik tiene que ver el fallo (504) para contarlo en el circuit breaker; si el
        # Orchestrator cortara primero, Traefik nunca registraría la respuesta.
        transportes = configuracion_dinamica(compose, comando_traefik)["http"]["serversTransports"]
        timeout = transportes[SERVERS_TRANSPORT]["forwardingTimeouts"]["responseHeaderTimeout"]

        assert segundos(timeout) == 8
        assert segundos(timeout) < float(entorno_orchestrator["HTTP_TIMEOUT_SECONDS"])

    def test_el_salto_publico_no_hereda_el_timeout_interno(self, comando_traefik):
        # Un timeout global también cortaría Cliente → Traefik → Orchestrator antes de que el
        # Orchestrator responda su Problem Details: el timeout es solo del transporte interno.
        assert not [arg for arg in comando_traefik if arg.lower().startswith("--serverstransport.")]
