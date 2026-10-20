# PDF Orchestrator Service

Punto de entrada del sistema PDF ExtractText. Recibe el PDF del cliente y coordina, en orden, a los demás microservicios.

## Flujo

```
Cliente
  ↓
Orchestrator
  ↓
Validator
  ↓
Extractor
  ↓
Persistence
  ↓
Base de datos (solo accesible por Persistence)
```

## Responsabilidad

El Orchestrator **solo coordina**:

1. Recibe el archivo del cliente.
2. Pide al **Validator** que lo valide. Si lo rechaza, corta el flujo.
3. Pide al **Extractor** el texto del PDF validado.
4. Pide a **Persistence** que guarde el resultado.
5. Devuelve la respuesta al cliente, o un error en formato RFC 9457.

Si un paso falla, los siguientes no se ejecutan. Traducir fallos de comunicación (servicio caído, timeout) a errores HTTP también es responsabilidad del Orchestrator.

### Qué NO hace

- **No valida PDFs**: lo hace el Validator. El Orchestrator no inspecciona el contenido del archivo.
- **No extrae texto**: lo hace el Extractor.
- **No accede a PostgreSQL**: el único servicio con acceso a la base de datos es Persistence.
- **No persiste directamente**: delega en Persistence.

## Arquitectura: Ports & Adapters

- **Ports** (`app/ports.py`): interfaces (`Protocol`) de Validator, Extractor y Persistence.
- **Caso de uso** (`app/services/orchestrator.py`): `OrchestratorService` coordina el flujo y depende únicamente de los ports.
- **Adapters** (`app/adapters/`): implementaciones concretas de los ports. `http.py` reúne lo común a los adapters HTTP (clientes compartidos, errores de transporte, lectura de problemas RFC 9457).
- **Presentación** (`app/routes.py`): endpoints FastAPI que delegan en el caso de uso.

¿Por qué es apropiada?

- **Aísla el caso de uso de HTTP**: la coordinación no conoce URLs, clientes ni formatos de transporte, que todavía no están acordados.
- **Inversión de dependencias**: el caso de uso depende de abstracciones, no de implementaciones concretas.
- **Adapters reemplazables**: los fakes actuales se sustituyen por adapters HTTP sin tocar el caso de uso.
- **Testing simple**: el flujo se prueba con fakes, sin levantar otros servicios.

### Sobre los fakes actuales

`app/dependencies.py` conecta el **adapter HTTP del Validator** (`app/adapters/validator.py`) y, por ahora, **fakes** (`app/adapters/fakes.py`) para Extractor y Persistence:

- existen solo para desarrollo y tests;
- **no representan la integración real** con los otros servicios (el fake del Extractor siempre devuelve un texto fijo);
- **antes de producción deben reemplazarse por adapters HTTP**, que se implementarán cuando los contratos estén acordados.

## Estructura del proyecto

```
.
├── main.py                     # create_app() y run(): entrypoint del servidor
├── app/
│   ├── config.py               # Settings: configuración desde variables de entorno
│   ├── dependencies.py         # Composición de dependencias (hoy: fakes)
│   ├── exceptions.py           # Errores de dominio y su código HTTP
│   ├── ports.py                # Ports (contratos internos) hacia los servicios
│   ├── problem_details.py      # Respuestas de error RFC 9457
│   ├── routes.py               # Endpoints HTTP
│   ├── schemas.py              # DTOs públicos
│   ├── adapters/
│   │   ├── http.py             # Clientes HTTP compartidos y errores de transporte
│   │   ├── validator.py        # Adapter HTTP del Validator
│   │   └── fakes.py            # Fakes para desarrollo y tests
│   └── services/
│       └── orchestrator.py     # Caso de uso OrchestratorService
├── tests/
│   ├── api/                    # Tests de endpoints (TestClient)
│   ├── unit/                   # Caso de uso, configuración y entrypoint
│   └── stubs.py                # Dobles de prueba auxiliares
├── Dockerfile
├── docker-compose.yml          # Solo el Orchestrator (ver sección Docker)
├── .dockerignore
├── .env.example
└── pyproject.toml
```

## API pública

### `POST /extraer`

Request `multipart/form-data` con el campo `file` (el archivo a procesar). El Orchestrator no revisa el tipo ni el contenido: eso lo decide el Validator.

Respuesta `200 OK`:

```json
{
  "exito": true,
  "texto": "...",
  "nombre_archivo": "..."
}
```

### `GET /health`

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
  "title": "Servicio no disponible",
  "status": 503,
  "detail": "El servicio Validator no está disponible.",
  "instance": "/extraer"
}
```

Los errores de validación del request incluyen además una lista `errors` con `loc`, `msg` y `type`.

| HTTP | Situación | Error de dominio |
|------|-----------|------------------|
| 422 | Request inválido (por ejemplo, falta `file`) | — (validación de FastAPI) |
| 400 / 413 / 415 / 422 | El Validator rechazó el archivo (se conservan su `status`, `type`, `title` y `detail`) | `ArchivoRechazadoError` |
| 502 | El Validator respondió fuera de contrato | `ValidacionFallidaError` |
| 502 | Falló la extracción de texto | `ExtraccionFallidaError` |
| 502 | Falló la persistencia | `PersistenciaFallidaError` |
| 503 | Un servicio downstream no está disponible | `ServicioNoDisponibleError` |
| 504 | Un servicio downstream no respondió a tiempo | `TiempoAgotadoError` |
| 404 / 405 | Ruta o método inexistente | — |
| 500 | Error inesperado | — |

- 503 y 504 son genéricos para cualquier servicio downstream. `errores_de_transporte()` (`app/adapters/http.py`) traduce los fallos de conexión y timeout de httpx con mensajes fijos; como `httpx.TimeoutException` es subclase de `httpx.TransportError`, captura primero el timeout.
- Nunca se exponen al cliente detalles internos (causas, trazas, hosts). El detalle de los 500 queda solo en el log, y los adapters deben usar mensajes fijos en `detail`, no el texto de la excepción original.

## Estado de contratos externos — ⚠️ PENDIENTE

> **Los contratos HTTP definitivos con Validator, Extractor y Persistence todavía deben ser acordados por el equipo.**
> Hasta entonces no se implementan adapters HTTP ni se documentan endpoints, DTOs, puertos o formatos de esos servicios.

| Servicio | Estado |
|----------|--------|
| Validator | Confirmado (rama `feat/http-contract`) e implementado, ver abajo |
| Extractor | **CONTRATO PENDIENTE DE ACUERDO CON EL EQUIPO** |
| Persistence | **CONTRATO PENDIENTE DE ACUERDO CON EL EQUIPO** |

Decisiones pendientes que dependen de ese acuerdo:

- endpoints, DTOs y formato de envío del archivo (multipart o JSON) de cada servicio;
- códigos de error de cada servicio y su traducción en el Orchestrator (por ejemplo, un 5xx del Validator hoy no tiene error propio);
- si Persistence devuelve un identificador que el Orchestrator deba exponer;
- límite de tamaño de archivo y quién lo aplica;
- estándares compartidos: RFC 9457, campo `exito`, `/health` con o sin barra final.

### Contratos internos provisorios (ports)

Son las firmas Python que usa el caso de uso. **No son contratos HTTP** y pueden cambiar cuando se acuerden los contratos con cada servicio.

| Port | Firma | En caso de fallo |
|------|-------|------------------|
| `Validator` | `validar(nombre_archivo: str, contenido: bytes) -> None` | `ArchivoRechazadoError`, `ValidacionFallidaError` |
| `Extractor` | `extraer(nombre_archivo: str, contenido: bytes) -> str` | `ExtraccionFallidaError` |
| `Persistence` | `guardar(nombre_archivo: str, texto: str) -> None` | `PersistenciaFallidaError` |

Cualquier port puede además lanzar `ServicioNoDisponibleError` o `TiempoAgotadoError`.

### Validator (`app/adapters/validator.py`)

`POST /validate` con el archivo en el campo multipart `file`, siempre con Content-Type `application/pdf`.

| Respuesta del Validator | Resultado en el Orchestrator |
|-------------------------|------------------------------|
| `200` | Archivo válido; no se depende del cuerpo |
| `400` / `413` / `415` / `422` `application/problem+json` con `type` `/errors/empty-file`, `/errors/file-too-large`, `/errors/unsupported-file-type` o `/errors/invalid-pdf`, y `title`/`detail` de texto | Se propaga al cliente con el mismo `status`, `type`, `title` y `detail` |
| `400` `/errors/bad-request` | `502`: el Orchestrator armó mal el request; no es culpa del cliente |
| `503` | `503` servicio no disponible |
| Timeout / fallo de conexión | `504` / `503` |
| Cualquier otra respuesta (5xx, 404, cuerpo fuera de contrato) | `502` `ValidacionFallidaError` |

## Configuración

Se centraliza en `app/config.py` (`Settings`, basada en `pydantic-settings`). Se lee de variables de entorno o de un archivo `.env` opcional (ver `.env.example`).

| Variable | Obligatoria | Por defecto | Descripción |
|----------|-------------|-------------|-------------|
| `VALIDATOR_URL` | Sí | — | URL base del Validator |
| `EXTRACTOR_URL` | Sí | — | URL base del Extractor |
| `PERSISTENCE_URL` | Sí | — | URL base de Persistence |
| `HTTP_TIMEOUT_SECONDS` | No | `10` | Timeout por llamada HTTP (> 0) |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL` |
| `PORT` | No | `8000` | Puerto del Orchestrator |

- Estas variables son **configuración propuesta por el Orchestrator, no contratos definitivos del equipo**.
- Las URLs aceptan hostnames de Docker (por ejemplo `http://validator:8000`) y no tienen valor por defecto. `HttpUrl` normaliza agregando `/` final, así que los adapters deberán usar `httpx.AsyncClient(base_url=...)` con paths relativos.
- `main.run()` construye `Settings` al arrancar el servidor: si falta una variable obligatoria o hay un valor inválido, el proceso falla **antes** de levantar. Usa `PORT` y `LOG_LEVEL`.
- El `lifespan` de FastAPI vuelve a leer `Settings` y abre un `httpx.AsyncClient` compartido por servicio (`app/adapters/http.py`), con su URL como `base_url` y `HTTP_TIMEOUT_SECONDS` como timeout. Los cierra al apagar. Si la configuración es inválida, la app no arranca.
- Importar la app (`main:app`) no lee la configuración: los tests que no ejecutan el lifespan no necesitan `.env`.

## Desarrollo

Requiere Python 3.12 y [uv](https://docs.astral.sh/uv/).

```bash
uv sync                          # instalar dependencias
uv run uvicorn main:app --reload # desarrollo con recarga (no requiere variables)
cp .env.example .env             # o exportar las variables
uv run python main.py            # arranque como en producción (valida Settings)
```

## Tests

```bash
uv run pytest                    # suite completa
uv run pytest tests/unit         # caso de uso, configuración y entrypoint
uv run pytest tests/api          # endpoints y RFC 9457
uv run ruff check .              # lint
```

Los tests no levantan otros servicios: usan los fakes de `app/adapters/fakes.py`, los dobles de `tests/stubs.py` y, para los adapters HTTP, `httpx.MockTransport` (incluido en httpx, sin dependencias extra).

## Docker

**Dockerfile** (`python:3.12-slim` + `uv`):

- instala exactamente `uv.lock` (`uv sync --frozen --no-dev`), sin dependencias de desarrollo;
- corre como usuario no-root (`appuser`);
- arranca con `python main.py`, que valida la configuración y respeta `PORT` y `LOG_LEVEL`;
- `HEALTHCHECK` contra `GET /health` en el `PORT` configurado.

```bash
docker build -t pdf-orchestrator .
docker run --rm --env-file .env -p 8000:8000 pdf-orchestrator
```

**`docker-compose.yml`** levanta **solo el Orchestrator**, para desarrollo local y como smoke test de la imagen (`cp .env.example .env && docker compose up --build`). No integra a los demás servicios: hoy el Orchestrator usa fakes y las URLs de `.env` no se contactan.

**Pendiente de acuerdo con el equipo:**

- **Compose general**: en qué repositorio vive y cómo se declaran Validator, Extractor y Persistence.
- **Red interna Docker**: el nombre de la red compartida no está acordado, por eso el compose de este repo no declara ninguna red. La propuesta es una red bridge común en la que solo el Orchestrator publique un puerto hacia el host, y Validator, Extractor y Persistence queden accesibles únicamente por la red interna.
- **Nombres de servicio y puertos internos** de cada microservicio, que definen los valores reales de `*_URL`.

## Estado del Día 1

| Tarea | Estado |
|-------|--------|
| Analizar arquitectura | Completa |
| Responsabilidad del Orchestrator | Completa |
| Comunicación con Validator / Extractor / Persistence | Bloqueada: contratos pendientes (ports internos definidos) |
| DTOs | Parcial: DTOs públicos definidos; DTOs de integración bloqueados por contratos |
| Errores | Completa para el Orchestrator; errores propios de cada servicio bloqueados por contratos |
| Configuración por variables de entorno | Completa (nombres propuestos, sujetos a acuerdo) |
| Estructura FastAPI | Completa |
| Dockerfile | Completa |
| Docker Compose | Parcial: compose standalone listo; integración al compose general pendiente |
| Red interna Docker | Bloqueada: nombre de red pendiente de acuerdo |
| Revisión SOLID / KISS / DRY / YAGNI | Completa |
| Tests iniciales, casos principales y de error | Completa |
