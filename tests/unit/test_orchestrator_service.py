import pytest

from app.adapters.fakes import FakeExtractor, FakePersistence, FakeValidator
from app.exceptions import ExtraccionFallidaError, PdfInvalidoError, PersistenciaFallidaError
from app.services.orchestrator import OrchestratorService, ResultadoExtraccion

pytestmark = pytest.mark.anyio

NOMBRE = "documento.pdf"
CONTENIDO = b"%PDF-1.7 contenido"


def crear_servicio(validator=None, extractor=None, persistence=None):
    validator = validator or FakeValidator()
    extractor = extractor or FakeExtractor()
    persistence = persistence or FakePersistence()
    servicio = OrchestratorService(validator=validator, extractor=extractor, persistence=persistence)
    return servicio, validator, extractor, persistence


async def test_llama_primero_al_validator_con_el_archivo():
    servicio, validator, _, _ = crear_servicio()

    await servicio.procesar(NOMBRE, CONTENIDO)

    assert validator.llamadas == [(NOMBRE, CONTENIDO)]


async def test_si_validator_rechaza_lanza_pdf_invalido_con_motivo():
    servicio, *_ = crear_servicio(validator=FakeValidator(es_valido=False, motivo="No es un PDF"))

    with pytest.raises(PdfInvalidoError, match="No es un PDF"):
        await servicio.procesar(NOMBRE, CONTENIDO)


async def test_si_validator_rechaza_no_llama_extractor_ni_persistence():
    servicio, _, extractor, persistence = crear_servicio(validator=FakeValidator(es_valido=False))

    with pytest.raises(PdfInvalidoError):
        await servicio.procesar(NOMBRE, CONTENIDO)

    assert extractor.llamadas == []
    assert persistence.llamadas == []


async def test_si_extractor_falla_no_llama_persistence():
    servicio, _, _, persistence = crear_servicio(extractor=FakeExtractor(falla=True))

    with pytest.raises(ExtraccionFallidaError):
        await servicio.procesar(NOMBRE, CONTENIDO)

    assert persistence.llamadas == []


async def test_si_persistence_falla_propaga_el_error():
    servicio, *_ = crear_servicio(persistence=FakePersistence(falla=True))

    with pytest.raises(PersistenciaFallidaError):
        await servicio.procesar(NOMBRE, CONTENIDO)


async def test_flujo_exitoso_extrae_y_persiste_el_texto():
    servicio, _, extractor, persistence = crear_servicio(extractor=FakeExtractor(texto="hola mundo"))

    await servicio.procesar(NOMBRE, CONTENIDO)

    assert extractor.llamadas == [(NOMBRE, CONTENIDO)]
    assert persistence.llamadas == [(NOMBRE, "hola mundo")]


async def test_flujo_exitoso_devuelve_texto_y_nombre_archivo():
    servicio, *_ = crear_servicio(extractor=FakeExtractor(texto="hola mundo"))

    resultado = await servicio.procesar(NOMBRE, CONTENIDO)

    assert resultado == ResultadoExtraccion(nombre_archivo=NOMBRE, texto="hola mundo")
