# PDF Orchestrator Service

Punto de entrada del sistema PDF ExtractText. Recibe el PDF del cliente y coordina, en orden, a los demás microservicios:

```
Cliente → Orchestrator → Validator → Extractor → Persistence → Base de datos
```

## Responsabilidad

El Orchestrator **solo coordina**:

1. Recibe el archivo del cliente.
2. Pide al **Validator** que lo valide. Si lo rechaza, corta el flujo.
3. Pide al **Extractor** el texto del PDF validado.
4. Pide a **Persistence** que guarde el resultado.
5. Devuelve la respuesta al cliente, o un error en formato RFC 9457.

Si un paso falla, los siguientes no se ejecutan.

### Qué NO hace

- **No valida PDFs**: lo hace el Validator. El Orchestrator no inspecciona el contenido del archivo.
- **No extrae texto**: lo hace el Extractor.
- **No accede a PostgreSQL**: el único servicio con acceso a la base de datos es Persistence.
- **No persiste directamente**: delega en Persistence.

## Arquitectura: Ports & Adapters

- **Ports** (`app/ports.py`): interfaces (`Protocol`) de Validator, Extractor y Persistence.
- **Caso de uso** (`app/services/orchestrator.py`): `OrchestratorService` coordina el flujo y depende únicamente de los ports.
- **Adapters** (`app/adapters/`): implementaciones concretas de los ports.
- **Presentación** (`app/routes.py`): endpoints FastAPI que delegan en el caso de uso.

¿Por qué es apropiada?

- El caso de uso depende de abstracciones, no de implementaciones concretas.
- Permite reemplazar los fakes actuales por adapters HTTP sin tocar el caso de uso.
- Facilita el testing: el flujo se prueba con dobles, sin levantar otros servicios.
- Evita acoplar la lógica de coordinación a HTTP o a un cliente concreto.

### Sobre los fakes actuales

`app/dependencies.py` conecta hoy **fakes** (`app/adapters/fakes.py`) como implementación por defecto:

- existen solo para desarrollo y tests;
- **no representan la integración real** con los otros servicios (el fake del Extractor siempre devuelve un texto fijo);
- **antes de producción deben reemplazarse por adapters HTTP**, que se implementarán cuando los contratos estén acordados.

## Estructura del proyecto

```
.
├── main.py                     # create_app(): handlers de error + router
├── app/
│   ├── config.py               # Settings: configuración desde variables de entorno
│   ├── dependencies.py         # Composición de dependencias (hoy: fakes)
│   ├── exceptions.py           # Errores de dominio y su código HTTP
│   ├── ports.py                # Ports hacia Validator, Extractor y Persistence
│   ├── problem_details.py      # Respuestas de error RFC 9457
│   ├── routes.py               # Endpoints HTTP
│   ├── schemas.py              # DTOs públicos
│   ├── adapters/
│   │   └── fakes.py            # Fakes para desarrollo y tests
│   └── services/
│       └── orchestrator.py     # Caso de uso OrchestratorService
├── tests/
│   ├── api/                    # Tests de endpoints (TestClient)
│   ├── unit/                   # Tests del caso de uso y de la configuración
│   └── stubs.py                # Dobles de prueba auxiliares
├── .env.example
└── pyproject.toml
```

## API pública

### `POST /extraer`

Request `multipart/form-data` con el campo `file` (el archivo a procesar).

Respuesta `200 OK`:

```json
{
  "exito": true,
  "texto": "...",
  "nombre_archivo": "..."
}
```

### `GET /health/`

Indica que el Orchestrator está vivo. No consulta a otros servicios.

Respuesta `200 OK`:

```json
{ "status": "ok" }
```

## Manejo de errores (RFC 9457)

Todos los errores se devuelven con `Content-Type: application/problem+json`:

```json
{
  "type": "about:blank",
  "title": "PDF inválido",
  "status": 422,
  "detail": "El archivo no es un PDF válido.",
  "instance": "/extraer"
}
```

Los errores de validación del request incluyen además una lista `errors` con `loc`, `msg` y `type`.

| HTTP | Situación | Error de dominio |
|------|-----------|------------------|
| 422 | Request inválido (por ejemplo, falta `file`) | — (validación de FastAPI) |
| 422 | El Validator rechazó el archivo | `PdfInvalidoError` |
| 502 | Falló la extracción de texto | `ExtraccionFallidaError` |
| 502 | Falló la persistencia | `PersistenciaFallidaError` |
| 503 | Un servicio downstream no está disponible | `ServicioNoDisponibleError` |
| 504 | Un servicio downstream no respondió a tiempo | `TiempoAgotadoError` |
| 404 / 405 | Ruta o método inexistente | — |
| 500 | Error inesperado | — |

Los errores 503 y 504 son infraestructura genérica para cualquier servicio downstream; los adapters HTTP futuros deberán traducir sus fallos de conexión y timeout a estos errores. Nunca se exponen al cliente detalles internos (causas, trazas, hosts): el detalle de los 500 queda solo en el log.

## Contratos con otros servicios

| Servicio | Estado |
|----------|--------|
| Validator | **CONTRATO PENDIENTE DE ACUERDO CON EL EQUIPO** |
| Extractor | **CONTRATO PENDIENTE DE ACUERDO CON EL EQUIPO** |
| Persistence | **CONTRATO PENDIENTE DE ACUERDO CON EL EQUIPO** |

Todavía no están definidos los endpoints, DTOs ni formatos HTTP de estos servicios.

### Contratos internos provisorios (ports)

Son las firmas Python que usa el caso de uso. **Pueden cambiar** cuando se acuerden los contratos HTTP.

| Port | Firma | En caso de fallo |
|------|-------|------------------|
| `Validator` | `validar(nombre_archivo: str, contenido: bytes) -> ResultadoValidacion(es_valido, motivo)` | — |
| `Extractor` | `extraer(nombre_archivo: str, contenido: bytes) -> str` | `ExtraccionFallidaError` |
| `Persistence` | `guardar(nombre_archivo: str, texto: str) -> None` | `PersistenciaFallidaError` |

Cualquier port puede además lanzar `ServicioNoDisponibleError` o `TiempoAgotadoError`.

## Configuración

Se centraliza en `app/config.py` (`Settings`, basada en `pydantic-settings`). Se lee de variables de entorno o de un archivo `.env` (ver `.env.example`).

| Variable | Obligatoria | Por defecto | Descripción |
|----------|-------------|-------------|-------------|
| `VALIDATOR_URL` | Sí | — | URL base del Validator |
| `EXTRACTOR_URL` | Sí | — | URL base del Extractor |
| `PERSISTENCE_URL` | Sí | — | URL base de Persistence |
| `HTTP_TIMEOUT_SECONDS` | No | `10` | Timeout por llamada HTTP (> 0) |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL` |
| `PORT` | No | `8000` | Puerto del Orchestrator |

Estas variables son **configuración propuesta por el Orchestrator, no contratos definitivos del equipo**. Por ahora `Settings` no está conectada a la aplicación: la usarán los adapters HTTP.

## Desarrollo

Requiere Python 3.12 y [uv](https://docs.astral.sh/uv/).

```bash
uv sync                          # instalar dependencias
uv run uvicorn main:app --reload # levantar el servicio (con fakes)
uv run pytest                    # tests
uv run ruff check .              # lint
```
