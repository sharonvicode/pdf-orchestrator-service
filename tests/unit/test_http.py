import httpx
import pytest

from app.adapters.http import errores_de_transporte, retry_after_valido
from app.exceptions import ServicioNoDisponibleError, TiempoAgotadoError


@pytest.mark.parametrize("timeout", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout])
def test_timeout_se_traduce_a_tiempo_agotado(timeout):
    # ConnectTimeout también es TransportError: el timeout debe ganar.
    with pytest.raises(TiempoAgotadoError, match="El servicio Validator no respondió a tiempo."):
        with errores_de_transporte("Validator"):
            raise timeout("timed out")


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError])
def test_error_de_transporte_se_traduce_a_servicio_no_disponible(error):
    with pytest.raises(ServicioNoDisponibleError, match="El servicio Validator no está disponible."):
        with errores_de_transporte("Validator"):
            raise error("connection refused")


def test_el_mensaje_es_fijo_y_conserva_la_causa():
    original = httpx.ConnectError("connection refused: http://10.0.0.5:9000")

    with pytest.raises(ServicioNoDisponibleError) as error:
        with errores_de_transporte("Validator"):
            raise original

    assert "10.0.0.5" not in error.value.detail
    assert error.value.__cause__ is original


def test_otros_errores_no_se_traducen():
    with pytest.raises(ValueError):
        with errores_de_transporte("Validator"):
            raise ValueError("otro error")


@pytest.mark.parametrize(
    "valor",
    ["0", "1", "120", "Wed, 21 Oct 2015 07:28:00 GMT", "Sun, 06 Nov 1994 08:49:37 GMT"],
)
def test_retry_after_acepta_delay_seconds_e_imf_fixdate(valor):
    assert retry_after_valido(valor) == valor


@pytest.mark.parametrize(
    "valor",
    [
        None,
        "",
        "pronto",
        "-1",
        "1.5",
        "+5",
        " 5",
        "5\n",
        "Wed, 32 Oct 2015 07:28:00 GMT",
        "Wed, 21 Oct 2015 25:28:00 GMT",
        "Wed, 21 Oct 2015 07:28:00 UTC",
        "wed, 21 Oct 2015 07:28:00 GMT",
        "Wednesday, 21-Oct-15 07:28:00 GMT",
        "Wed Oct 21 07:28:00 2015",
    ],
)
def test_retry_after_ausente_o_invalido_se_descarta(valor):
    assert retry_after_valido(valor) is None
