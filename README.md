# PDF Orchestrator Service

Punto de entrada público del sistema PDF ExtractText. Recibe el PDF del cliente y coordina, en orden, a los demás microservicios por HTTP.

## Flujo

```
Cliente
  ↓
Orchestrator
  ↓
Validator      POST /validate
  ↓
Extractor      POST /api/v1/extraer
  ↓
Persistence    POST /extractions
  ↓
MongoDB (solo accesible por Persistence)
```

## Responsabilidad

El Orchestrator **solo coordina**:

1. Recibe el archivo del cliente.
2. Pide al **Validator** que lo valide. Si lo rechaza, corta el flujo.
3. Pide al **Extractor** el texto y la cantidad de páginas del PDF validado.
4. Pide a **Persistence** que guarde el nombre, el texto y la cantidad de páginas, y obtiene el `id` generado.
5. Devuelve la respuesta al cliente, o un error en formato RFC 9457.

Si un paso falla, los siguientes no se ejecutan. Traducir las respuestas y los fallos de comunicación de cada servicio a errores HTTP también es responsabilidad del Orchestrator. No hay reintentos automáticos: el alta en Persistence no es idempotente.

### Qué NO hace

- **No valida PDFs**: lo hace el Validator. El Orchestrator no inspecciona el contenido del archivo.
- **No extrae texto**: lo hace el Extractor.
- **No accede a MongoDB**: el único servicio con acceso a la base de datos es Persistence.

## Arquitectura: Ports & Adapters

- **Ports** (`app/ports.py`): interfaces (`Protocol`) de Validator, Extractor y Persistence.
- **Caso de uso** (`app/services/orchestrator.py`): `OrchestratorService` coordina el flujo y depende únicamente de los ports.
- **Adapters HTTP** (`app/adapters/`): traducen cada port al contrato HTTP de su servicio. `http.py` reúne lo común: clientes compartidos, errores de transporte, lectura de problemas RFC 9457 y validación de `Retry-After`.
- **Composición** (`app/dependencies.py`): conecta los tres adapters HTTP con los clientes que abre el `lifespan`.
- **Presentación** (`app/routes.py`): endpoints FastAPI que delegan en el caso de uso.

El caso de uso no conoce URLs ni formatos de transporte, y se prueba con los fakes de `app/adapters/fakes.py`, que **no forman parte de la composición por defecto**.

### Ports

| Port | Firma | Errores propios |
|------|-------|-----------------|
| `Validator` | `validar(nombre_archivo: str, contenido: bytes) -> None` | `ArchivoRechazadoError`, `ValidacionFallidaError` |
| `Extractor` | `extraer(nombre_archivo: str, contenido: bytes) -> TextoExtraido(texto, cantidad_paginas)` | `ArchivoRechazadoError`, `ExtraccionFallidaError` |
| `Persistence` | `guardar(nombre_archivo: str, texto: str, cantidad_paginas: int) -> str` (el `id`) | `PersistenciaFallidaError` |

Cualquier port puede además lanzar `ServicioNoDisponibleError` o `TiempoAgotadoError`.

## Estructura del proyecto

```
.
├── main.py                     # create_app(), lifespan y run(): entrypoint del servidor
├── app/
│   ├── config.py               # Settings: configuración desde variables de entorno
│   ├── dependencies.py         # Composición: adapters HTTP sobre los clientes compartidos
│   ├── exceptions.py           # Errores de dominio y su código HTTP
│   ├── ports.py                # Ports (contratos internos) hacia los servicios
│   ├── problem_details.py      # Respuestas de error RFC 9457
│   ├── routes.py               # Endpoints HTTP
│   ├── schemas.py              # DTOs públicos
│   ├── adapters/
│   │   ├── http.py             # Infraestructura HTTP común
│   │   ├── validator.py        # Adapter HTTP del Validator
│   │   ├── extractor.py        # Adapter HTTP del Extractor
│   │   ├── persistence.py      # Adapter HTTP de Persistence
│   │   └── fakes.py            # Fakes para los tests
│   └── services/
│       └── orchestrator.py     # Caso de uso OrchestratorService
├── tests/
│   ├── api/                    # Endpoints, RFC 9457 y flujo HTTP completo (TestClient)
│   ├── unit/                   # Caso de uso, adapters, configuración y entrypoint
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
  "id": "6f1c2a9e-8f0e-4b8e-9a51-2b7c3d4e5f60",
  "texto": "...",
  "nombre_archivo": "documento.pdf"
}
```

`id` es el identificador que generó Persistence.

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
| 400 / 413 / 415 / 422 | El Validator rechazó el archivo | `ArchivoRechazadoError` |
| 413 / 422 | El Extractor rechazó el archivo | `ArchivoRechazadoError` |
| 502 | El Validator respondió fuera de contrato | `ValidacionFallidaError` |
| 502 | El Extractor respondió fuera de contrato | `ExtraccionFallidaError` |
| 502 | Persistence respondió fuera de contrato | `PersistenciaFallidaError` |
| 503 | Un servicio no está disponible (con `Retry-After` si el Extractor envía uno válido) | `ServicioNoDisponibleError` |
| 504 | Un servicio no respondió a tiempo | `TiempoAgotadoError` |
| 404 / 405 | Ruta o método inexistente | — |
| 500 | Error inesperado | — |

- `errores_de_transporte()` (`app/adapters/http.py`) traduce los timeouts de httpx a 504 y los fallos de conexión a 503, con mensajes fijos. Como `httpx.TimeoutException` es subclase de `httpx.TransportError`, captura primero el timeout.
- Nunca se exponen al cliente detalles internos (causas, trazas, hosts, mensajes de bibliotecas). Los 500 se registran en el log; los 502 usan mensajes fijos.

## Contratos con los servicios

Todos los archivos se envían en el campo multipart `file`, con el nombre original y Content-Type `application/pdf`.

### Validator (`app/adapters/validator.py`)

`POST /validate`. Contrato publicado en la rama `feat/http-contract` del Validator.

| Respuesta del Validator | Resultado en el Orchestrator |
|-------------------------|------------------------------|
| `200` | Archivo válido; no se depende del cuerpo |
| `400` / `413` / `415` / `422` `application/problem+json` con `type` `/errors/empty-file`, `/errors/file-too-large`, `/errors/unsupported-file-type` o `/errors/invalid-pdf`, y `title`/`detail` de texto | Se propaga al cliente con el mismo `status`, `type`, `title` y `detail` |
| `400` `/errors/bad-request` | `502`: el Orchestrator armó mal el request; no es culpa del cliente |
| `503` | `503` servicio no disponible |
| Timeout / fallo de conexión | `504` / `503` |
| Cualquier otra respuesta (5xx, 404, cuerpo fuera de contrato) | `502` `ValidacionFallidaError` |

### Extractor (`app/adapters/extractor.py`)

`POST /api/v1/extraer`. Del cuerpo de éxito solo se usan `texto` y `metadatos.cantidad_paginas`; los demás metadatos se toleran pero no se exigen.

| Respuesta del Extractor | Resultado en el Orchestrator |
|-------------------------|------------------------------|
| `200` con `texto` (texto) y `metadatos.cantidad_paginas` (entero ≥ 0) | Sigue el flujo |
| `413` `application/problem+json` con `title`/`detail` de texto | Se propaga con el mismo `status`, `title` y `detail`, y `type` `about:blank` |
| `422` `application/problem+json` con `title`/`detail` de texto | Se propaga con el mismo `status` y `title`, `type` `about:blank` y un `detail` fijo: el original puede incluir mensajes internos de la biblioteca de PDF |
| `422` con la extensión `errors` | `502`: es la validación del request del Extractor, el Orchestrator lo armó mal |
| `503` | `503`; se reenvía `Retry-After` solo si es válido (segundos o IMF-fixdate, RFC 9110) |
| Timeout / fallo de conexión | `504` / `503` |
| Cualquier otra respuesta, o `200` fuera de contrato | `502` `ExtraccionFallidaError` con mensaje fijo |

### Persistence (`app/adapters/persistence.py`)

`POST /extractions` con JSON `{"file_name": "...", "text": "...", "page_count": 3}`; responde `201` con `{"id": "<uuid>"}`.

Persistence no garantiza RFC 9457: sus errores pueden ser JSON de FastAPI o texto plano. Por eso su cuerpo de error no se interpreta ni se reenvía.

| Respuesta de Persistence | Resultado en el Orchestrator |
|--------------------------|------------------------------|
| `201` con `id` de texto no vacío | Se devuelve ese `id` al cliente |
| Timeout / fallo de conexión | `504` / `503` |
| Cualquier otro status (incluido otro 2xx), cuerpo ilegible, `id` ausente, vacío o que no sea texto | `502` `PersistenciaFallidaError` con mensaje fijo |

No se reintenta: un segundo `POST` crearía otra extracción.

## Configuración

Se centraliza en `app/config.py` (`Settings`, basada en `pydantic-settings`). Se lee de variables de entorno o de un archivo `.env` opcional (ver `.env.example`).

| Variable | Obligatoria | Por defecto | Descripción |
|----------|-------------|-------------|-------------|
| `VALIDATOR_URL` | Sí | — | URL base del Validator |
| `EXTRACTOR_URL` | Sí | — | URL base del Extractor |
| `PERSISTENCE_URL` | Sí | — | URL base de Persistence |
| `HTTP_TIMEOUT_SECONDS` | No | `10` | Timeout de httpx por llamada a un servicio (> 0) |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL` |
| `PORT` | No | `8000` | Puerto del Orchestrator |

- Las URLs aceptan hostnames de Docker (por ejemplo `http://validator:8000`) y no tienen valor por defecto. `HttpUrl` normaliza agregando `/` final; los adapters usan `httpx.AsyncClient(base_url=...)`.
- `main.run()` construye `Settings` al arrancar el servidor: si falta una variable obligatoria o hay un valor inválido, el proceso falla **antes** de levantar. Usa `PORT` y `LOG_LEVEL`.
- El `lifespan` de FastAPI vuelve a leer `Settings` y abre un `httpx.AsyncClient` compartido por servicio, con su URL como `base_url` y `HTTP_TIMEOUT_SECONDS` como timeout. Los cierra al apagar. Si la configuración es inválida, la app no arranca.
- Importar la app (`main:app`) no lee la configuración: los tests que no ejecutan el lifespan no necesitan `.env`.

### Timeout

httpx aplica `HTTP_TIMEOUT_SECONDS` **a cada fase** de la llamada (conexión, envío, espera de respuesta, pool), no como límite total.

Se mantiene en **10 s**, según lo medido en el Extractor (README y `TASK/tp-test-de-carga.md` de su repositorio):

- En reposo, una extracción de los PDFs oficiales tarda entre **41 y 147 ms** (hasta 90 páginas y 8.5 MB). 10 s son ~70 veces el peor caso.
- Cada proceso del Extractor extrae de a uno y encola hasta 20 pedidos; con la cola llena responde `503` al instante. Una cola llena de PDFs como esos se vacía en ~3 s, dentro del timeout.
- Bajo sobrecarga sostenida (50 req/s) la mediana llegó a ~14.7 s. Ahí subir el timeout no ayuda: el pedido sigue esperando en una cola saturada, y el `504` libera antes al cliente.

Revisar el valor con las pruebas de punta a punta (PDFs más pesados por CPU, como uno de 300 páginas, no tienen medición individual). Si se agota el timeout, el servicio puede terminar el trabajo igual: en particular, Persistence puede haber guardado la extracción aunque el cliente reciba `504`.

## Desarrollo

Requiere Python 3.12 y [uv](https://docs.astral.sh/uv/).

```bash
uv sync                          # instalar dependencias
cp .env.example .env             # o exportar las variables
uv run python main.py            # arranque como en producción (valida Settings)
uv run uvicorn main:app --reload # desarrollo con recarga (también requiere las variables)
```

## Tests

```bash
uv run pytest                    # suite completa
uv run pytest tests/unit         # caso de uso, adapters, configuración y entrypoint
uv run pytest tests/api          # endpoints, RFC 9457 y flujo HTTP completo
uv run ruff check .              # lint
uv run ruff format --check .     # formato
```

Los tests no levantan otros servicios:

- el caso de uso y los endpoints se prueban con los fakes de `app/adapters/fakes.py` y los dobles de `tests/stubs.py`;
- los adapters y el flujo completo (`tests/api/test_flujo_http.py`) usan `httpx.MockTransport`, incluido en httpx, para simular las respuestas de cada servicio y verificar los requests, el orden de las llamadas y la ausencia de reintentos.

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

**`docker-compose.yml`** levanta **solo el Orchestrator**, para desarrollo local y como smoke test de la imagen (`cp .env.example .env && docker compose up --build`). No incluye a Validator, Extractor ni Persistence: `POST /extraer` solo funciona si las `*_URL` de `.env` apuntan a servicios accesibles.

## Pendiente

- **Compose de integración** con Validator (rama `feat/http-contract`), Extractor, Persistence y MongoDB en una red interna, con solo el Orchestrator publicado hacia el host. Su ubicación y forma de referenciar los otros repositorios están por decidir.
- **Pruebas de punta a punta** con los servicios reales y PDFs reales, que confirmen también el timeout.
