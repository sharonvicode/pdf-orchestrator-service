import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from app.config import Settings


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    """Entorno aislado: sin .env del desarrollador, variables de Settings limpias y URLs cargadas."""
    monkeypatch.chdir(tmp_path)
    for nombre in Settings.model_fields:
        monkeypatch.delenv(nombre.upper(), raising=False)
    monkeypatch.setenv("VALIDATOR_URL", "http://validator.example")
    monkeypatch.setenv("EXTRACTOR_URL", "http://extractor.example")
    monkeypatch.setenv("PERSISTENCE_URL", "http://persistence.example")
    return monkeypatch


@pytest.fixture
def llamadas_a_uvicorn(monkeypatch):
    llamadas = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: llamadas.append((app, kwargs)))
    return llamadas


def test_run_levanta_uvicorn_con_puerto_y_log_level_de_settings(entorno, llamadas_a_uvicorn):
    entorno.setenv("PORT", "9000")
    entorno.setenv("LOG_LEVEL", "DEBUG")

    main.run()

    assert llamadas_a_uvicorn == [(main.app, {"host": "0.0.0.0", "port": 9000, "log_level": "debug"})]


def test_run_usa_valores_por_defecto(entorno, llamadas_a_uvicorn):
    main.run()

    assert llamadas_a_uvicorn == [(main.app, {"host": "0.0.0.0", "port": 8000, "log_level": "info"})]


def test_run_falla_antes_de_levantar_si_falta_configuracion(entorno, llamadas_a_uvicorn):
    entorno.delenv("VALIDATOR_URL")

    with pytest.raises(ValidationError):
        main.run()

    assert llamadas_a_uvicorn == []


def test_lifespan_abre_un_cliente_por_servicio_con_url_y_timeout_de_settings(entorno):
    entorno.setenv("HTTP_TIMEOUT_SECONDS", "2.5")
    app = main.create_app()

    with TestClient(app):
        clientes = app.state.clientes
        assert str(clientes.validator.base_url) == "http://validator.example/"
        assert str(clientes.extractor.base_url) == "http://extractor.example/"
        assert str(clientes.persistence.base_url) == "http://persistence.example/"
        for cliente in (clientes.validator, clientes.extractor, clientes.persistence):
            assert cliente.timeout == httpx.Timeout(2.5)
            assert not cliente.is_closed


def test_lifespan_cierra_los_clientes_al_apagar(entorno):
    app = main.create_app()

    with TestClient(app):
        clientes = app.state.clientes

    assert clientes.validator.is_closed
    assert clientes.extractor.is_closed
    assert clientes.persistence.is_closed


def test_lifespan_falla_al_arrancar_si_falta_configuracion(entorno):
    entorno.delenv("VALIDATOR_URL")

    with pytest.raises(ValidationError):
        with TestClient(main.create_app()):
            pass
