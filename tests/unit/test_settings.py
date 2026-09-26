from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

URLS = {
    "VALIDATOR_URL": "http://validator.example",
    "EXTRACTOR_URL": "http://extractor.example",
    "PERSISTENCE_URL": "http://persistence.example",
}
ENV_EXAMPLE = Path(__file__).parents[2] / ".env.example"


@pytest.fixture
def entorno(monkeypatch):
    """Entorno aislado: limpia las variables de Settings y carga solo las URLs."""
    for nombre in Settings.model_fields:
        monkeypatch.delenv(nombre.upper(), raising=False)
    for nombre, valor in URLS.items():
        monkeypatch.setenv(nombre, valor)
    return monkeypatch


def cargar() -> Settings:
    # Sin archivo .env: los tests dependen solo del entorno que arman.
    return Settings(_env_file=None)


def test_lee_las_urls_de_los_servicios_desde_el_entorno(entorno):
    settings = cargar()

    assert str(settings.validator_url).rstrip("/") == URLS["VALIDATOR_URL"]
    assert str(settings.extractor_url).rstrip("/") == URLS["EXTRACTOR_URL"]
    assert str(settings.persistence_url).rstrip("/") == URLS["PERSISTENCE_URL"]


def test_valores_por_defecto(entorno):
    settings = cargar()

    assert settings.http_timeout_seconds == 10.0
    assert settings.log_level == "INFO"
    assert settings.port == 8000


def test_lee_valores_opcionales_desde_el_entorno(entorno):
    entorno.setenv("HTTP_TIMEOUT_SECONDS", "2.5")
    entorno.setenv("LOG_LEVEL", "DEBUG")
    entorno.setenv("PORT", "9000")

    settings = cargar()

    assert settings.http_timeout_seconds == 2.5
    assert settings.log_level == "DEBUG"
    assert settings.port == 9000


@pytest.mark.parametrize("variable", list(URLS))
def test_url_de_servicio_es_obligatoria(entorno, variable):
    entorno.delenv(variable)

    with pytest.raises(ValidationError):
        cargar()


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("VALIDATOR_URL", "no-es-una-url"),
        ("EXTRACTOR_URL", "ftp://extractor.example"),
        ("HTTP_TIMEOUT_SECONDS", "0"),
        ("HTTP_TIMEOUT_SECONDS", "-1"),
        ("LOG_LEVEL", "VERBOSE"),
        ("PORT", "0"),
        ("PORT", "70000"),
    ],
)
def test_rechaza_valores_invalidos(entorno, variable, valor):
    entorno.setenv(variable, valor)

    with pytest.raises(ValidationError):
        cargar()


def test_env_example_documenta_todas_las_variables():
    documentadas = {
        linea.split("=", 1)[0].strip()
        for linea in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in linea and not linea.lstrip().startswith("#")
    }

    assert documentadas == {nombre.upper() for nombre in Settings.model_fields}
