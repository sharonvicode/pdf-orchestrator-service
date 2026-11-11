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

En la integración con Docker Compose, cada llamada del Orchestrator a Validator, Extractor y Persistence pasa por **Traefik**, que descubre los tres servicios a través de **Consul** (`consulCatalog`) y aplica un circuit breaker independiente por servicio (ver [Circuit breaker](#circuit-breaker)). El código del Orchestrator no lo sabe: solo cambian sus URLs.

## Responsabilidad

El Orchestrator **solo coordina**:

1. Recibe el archivo del cliente.
2. Pide al **Validator** que lo valide. Si lo rechaza, corta el flujo.
3. Pide al **Extractor** el texto y la cantidad de páginas del PDF validado.
4. Pide a **Persistence** que guarde el nombre, el texto y la cantidad de páginas, y obtiene el `id` generado.
5. Devuelve la respuesta al cliente, o un error en formato RFC 9457.

Si un paso falla, los siguientes no se ejecutan. Traducir las respuestas y los fallos de comunicación de cada servicio a errores HTTP también es responsabilidad del Orchestrator. No hay reintentos automáticos (ver [Persistence](#persistence-appadapterspersistencepy)).

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

### Revisión de principios (SOLID, KISS, DRY, YAGNI)

Registro de la revisión del diseño actual: qué se comprobó y qué límites se aceptaron. No es una garantía de que el diseño sea definitivo.

| Principio | Cómo se aplica | Límites conocidos |
|-----------|----------------|-------------------|
| **SRP** | `routes.py` solo recibe el archivo y arma la respuesta; `OrchestratorService.procesar` solo encadena `validar` → `extraer` → `guardar`; cada adapter traduce un único contrato HTTP. | Cada adapter hace la llamada y además traduce sus errores; se mantienen juntos porque la traducción depende del contrato de ese servicio. |
| **OCP / DIP** | `OrchestratorService` depende de los `Protocol` de `app/ports.py`, no de httpx; `app/dependencies.py` es el único lugar que elige los adapters HTTP. | Agregar un paso al flujo sí requiere cambiar el caso de uso: el flujo es fijo a propósito. |
| **LSP** | Los adapters HTTP, los fakes y los dobles de `tests/stubs.py` cumplen los mismos ports, y el caso de uso se prueba igual con cualquiera de ellos. | Los `Protocol` no se comprueban en ejecución; lo sostienen los tipos y los tests. |
| **ISP** | Un port por servicio, con un único método cada uno (`validar`, `extraer`, `guardar`). | — |
| **DRY** | `app/adapters/http.py` reúne lo común a los adapters: `errores_de_transporte`, `leer_problema`, `archivo_pdf`, `rechazo_del_archivo` y `no_disponible`. | `_rechazo` del Validator y del Extractor se parecen, pero no se unifican: cada uno aplica reglas distintas de su contrato. Los healthchecks de `docker-compose.integration.yml` repiten la misma línea. |
| **KISS** | Un `httpx.AsyncClient` por servicio, abierto en el `lifespan`; sin reintentos ni colas; el circuit breaker es de Traefik, no del código Python; los fallos se reducen a `502`, `503`, `504` y los rechazos propagados. | Un único `HTTP_TIMEOUT_SECONDS` para los tres servicios, aplicado por fase y no como límite total. |
| **YAGNI** | Sin caché ni reintentos; `/health` no consulta otros servicios; no hay variables de configuración sin uso. | `app/adapters/fakes.py` solo lo usan los tests, pero vive en el paquete `app` (ver abajo). |

**`app/adapters/fakes.py`.** Solo lo importan `tests/api/test_extraer.py` y `tests/unit/test_orchestrator_service.py`; ni `main.py` ni otro módulo de `app/` lo importa, así que no participa de la ejecución. Se incluye en la imagen porque el `Dockerfile` copia `app/` completo; es un archivo chico y sin dependencias propias. Moverlo a `tests/` sería más prolijo, pero obligaría a cambiar los imports de esas pruebas sin corregir ningún defecto, así que se deja donde está.

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
│   ├── unit/                   # Caso de uso, adapters, configuración, entrypoint y contrato del Compose de integración
│   ├── e2e/                    # Prueba de humo contra el stack de integración (Docker)
│   ├── stress/                 # Escenario k6 (k6-baseline.js) y resultados (results/BASELINE.md)
│   └── stubs.py                # Dobles de prueba auxiliares
├── TASKS.md                    # Estado de avance por día (Días 1 a 6) y tareas pendientes
├── Dockerfile
├── docker-compose.yml          # Solo el Orchestrator (ver sección Docker)
├── docker-compose.integration.yml  # Sistema completo con Traefik, Consul y MongoDB
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
- Con Traefik entre el Orchestrator y los servicios, varios fallos llegan como **respuestas** de Traefik y no como errores de transporte: su traducción está en [Respuestas de Traefik](#respuestas-de-traefik-y-su-traducción).

## Contratos con los servicios

Al Validator y al Extractor el archivo se les envía en el campo multipart `file`, con el nombre original y Content-Type `application/pdf`.

### Validator (`app/adapters/validator.py`)

`POST /validate`. Contrato publicado en la rama `feat/http-contract` del Validator.

| Respuesta del Validator | Resultado en el Orchestrator |
|-------------------------|------------------------------|
| `200` | Archivo válido; no se depende del cuerpo |
| `400` / `413` / `415` / `422` `application/problem+json` con `type` `/errors/empty-file`, `/errors/file-too-large`, `/errors/unsupported-file-type` o `/errors/invalid-pdf`, y `title`/`detail` de texto | Se propaga al cliente con el mismo `status`, `type`, `title` y `detail` |
| `400` `/errors/bad-request` | `502`: el Orchestrator armó mal el request; no es culpa del cliente |
| `503` | `503` servicio no disponible |
| Timeout / fallo de conexión | `504` / `503` |
| Cualquier otra respuesta (otro 5xx, 404, cuerpo fuera de contrato) | `502` `ValidacionFallidaError` con mensaje fijo |

### Extractor (`app/adapters/extractor.py`)

`POST /api/v1/extraer`. Del cuerpo de éxito solo se usan `texto` y `metadatos.cantidad_paginas`; los demás metadatos se toleran pero no se exigen.

| Respuesta del Extractor | Resultado en el Orchestrator |
|-------------------------|------------------------------|
| `200` con `texto` (string) y `metadatos.cantidad_paginas` (entero ≥ 0) | Sigue el flujo |
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
| `201` con `id` string no vacío; los campos adicionales se ignoran | Se devuelve ese `id` al cliente |
| Timeout / fallo de conexión | `504` / `503` |
| Cualquier otro status (incluidos otros 2xx y `503`, que no están en su contrato), cuerpo ilegible, `id` ausente, vacío, en blanco o que no sea string | `502` `PersistenciaFallidaError` con mensaje fijo |

**Sin reintentos.** El alta no es idempotente: un segundo `POST` crearía otra extracción. Si el timeout vence después de que Persistence guardó, el cliente recibe un error aunque la extracción exista (`502` si cortó Traefik, `504` si cortó el Orchestrator); **si el cliente reintenta, se crea un duplicado**. Un cliente que reintente tras ese error debe asumir ese riesgo.

### Respuestas de Traefik y su traducción

En la integración, el Orchestrator habla con Traefik y Traefik con cada servicio. Algunos fallos de un servicio llegan entonces como una respuesta HTTP **generada por Traefik**, en texto plano, que los adapters interpretan con las mismas reglas de las tablas anteriores. Los adapters no se modificaron para distinguirlas:

| Situación del servicio | Responde Traefik | Validator | Extractor | Persistence |
|------------------------|------------------|-----------|-----------|-------------|
| No responde a tiempo (por ejemplo, pausado) | `504` a los 8 s (`responseHeaderTimeout`) | `502` | `502` | `502` |
| Circuito abierto | `503` inmediato, sin `Retry-After` | `503` | `503` | `502` |
| Contenedor detenido (Traefik borra su ruta) | `404` | `502` | `502` | `502` |
| Inalcanzable con la ruta todavía registrada | `502` | `502` | `502` | `502` |

- El `504` de Traefik **no** se traduce a `504` `TiempoAgotadoError`: para los adapters es una respuesta fuera de contrato, así que el cliente recibe `502` con el mensaje fijo del servicio (por ejemplo, *"El Validator respondió fuera del contrato esperado."*). El `504` propio del Orchestrator (`HTTP_TIMEOUT_SECONDS`) queda como red de seguridad y en la práctica no se alcanza, porque Traefik corta antes.
- El `503` del circuito abierto llega como `503` *"Servicio no disponible"* desde Validator y Extractor, que ya traducían su `503`. Persistence no tiene `503` en su contrato y lo traduce a `502`.
- Distinguir estas respuestas (por ejemplo, `504` de Traefik → `504`) requeriría modificar los adapters; queda fuera del alcance (ver [Pendiente](#pendiente)).

## Configuración

Se centraliza en `app/config.py` (`Settings`, basada en `pydantic-settings`). Se lee de variables de entorno o de un archivo `.env` opcional (ver `.env.example`).

| Variable | Obligatoria | Por defecto | Descripción |
|----------|-------------|-------------|-------------|
| `VALIDATOR_URL` | Sí | — | URL base del Validator |
| `EXTRACTOR_URL` | Sí | — | URL base del Extractor |
| `PERSISTENCE_URL` | Sí | — | URL base de Persistence |
| `HTTP_TIMEOUT_SECONDS` | No | `10` | Timeout de httpx por fase de cada llamada a un servicio, en segundos (> 0) |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL` |
| `PORT` | No | `8000` | Puerto del Orchestrator |

- Las URLs aceptan hostnames de Docker (por ejemplo `http://validator:8000`) y no tienen valor por defecto. `HttpUrl` normaliza agregando `/` final; los adapters usan `httpx.AsyncClient(base_url=...)`.
- Una URL puede incluir un prefijo de ruta: httpx lo conserva y le agrega la ruta del contrato. En la integración se usan `http://traefik:8081/validator`, `http://traefik:8081/extractor` y `http://traefik:8081/persistence`, y el Orchestrator llama, por ejemplo, a `http://traefik:8081/validator/validate`.
- `main.run()` construye `Settings` al arrancar el servidor: si falta una variable obligatoria o hay un valor inválido, el proceso falla **antes** de levantar. Usa `PORT` y `LOG_LEVEL`.
- El `lifespan` de FastAPI vuelve a leer `Settings` y abre un `httpx.AsyncClient` compartido por servicio, con su URL como `base_url` y `HTTP_TIMEOUT_SECONDS` como timeout. Los cierra al apagar. Si la configuración es inválida, la app no arranca.
- Importar la app (`main:app`) no lee la configuración: los tests que no ejecutan el lifespan no necesitan `.env`.

### Timeout

httpx aplica `HTTP_TIMEOUT_SECONDS` **a cada fase** de la llamada (conexión, envío, espera de respuesta, pool), no como límite total.

Se mantiene en **10 s**, según lo medido en el Extractor (README y `TASK/tp-test-de-carga.md` de su repositorio):

- En reposo, una extracción de los PDFs oficiales tarda entre **41 y 147 ms** (hasta 90 páginas y 8.5 MB). 10 s son ~70 veces el peor caso.
- Cada proceso del Extractor extrae de a uno y encola hasta 20 pedidos; con la cola llena responde `503` al instante. Una cola llena de PDFs como esos se vacía en ~3 s, dentro del timeout.
- Bajo sobrecarga sostenida (50 req/s) la mediana llegó a ~14.7 s. Ahí subir el timeout no ayuda: el pedido sigue esperando en una cola saturada, y el `504` libera antes al cliente.

Revisar el valor con las pruebas de punta a punta (PDFs más pesados por CPU, como uno de 300 páginas, no tienen medición individual). Un servicio puede terminar su trabajo aunque el Orchestrator ya haya respondido; para Persistence eso implica el riesgo de duplicados descrito en su contrato.

**Con Traefik en el medio** hay dos timeouts, y el de Traefik es menor a propósito:

| Timeout | Valor | Dónde | Efecto |
|---------|-------|-------|--------|
| `responseHeaderTimeout` | `8s` | `serversTransport` `interno` de Traefik | Corta la espera de los headers del servicio y responde `504`, que **cuenta para el circuit breaker**. |
| `HTTP_TIMEOUT_SECONDS` | `10` | Orchestrator (httpx, por fase) | Red de seguridad. Si cortara primero, Traefik nunca registraría el fallo y el circuito no se abriría. |

Un pedido encolado por el backpressure del Extractor que espere más de 8 s también recibe ese `504` y cuenta como error de red (ver [Circuit breaker](#circuit-breaker)).

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

Los tests de `tests/unit` y `tests/api` no levantan otros servicios:

- el caso de uso y los endpoints se prueban con los fakes de `app/adapters/fakes.py` y los dobles de `tests/stubs.py`;
- los adapters y el flujo completo (`tests/api/test_flujo_http.py`) usan `httpx.MockTransport`, incluido en httpx, para simular las respuestas de cada servicio y verificar los requests, el orden de las llamadas y la ausencia de reintentos.

`tests/unit/test_compose_integracion.py` lee `docker-compose.integration.yml` con PyYAML y `json` (incluyendo las labels de Docker del Orchestrator, las variables de autorregistro `CONSUL_URL` / `CONSUL_SERVICE_TAGS` de los tres servicios internos y los providers de Traefik) y fija la configuración de descubrimiento dinámico y circuit breaker sin levantar contenedores (ver [Circuit breaker](#circuit-breaker)).

`tests/e2e` es la prueba de humo contra el sistema real (ver [Integración con Docker Compose](#integración-con-docker-compose)); sin sus variables de entorno se salta.

## Docker

**Dockerfile** (`python:3.12-slim` + `uv 0.13.7`):

- instala exactamente `uv.lock` (`uv sync --frozen --no-dev`, incluyendo `certifi 2026.10.19`), sin dependencias de desarrollo;
- desinstala `pip` de la imagen base (`pip uninstall -y pip`), eliminando paquetes sin uso y sus CVEs asociados;
- corre como usuario no-root (`appuser`);
- arranca con `python main.py`, que valida la configuración y respeta `PORT` y `LOG_LEVEL`;
- `HEALTHCHECK` contra `GET /health` en el `PORT` configurado.

```bash
docker build -t pdf-orchestrator .
docker run --rm --env-file .env -p 8000:8000 pdf-orchestrator
```

**`docker-compose.yml`** levanta **solo el Orchestrator**, para desarrollo local y como smoke test de la imagen (`cp .env.example .env && docker compose up --build`). No incluye a Validator, Extractor ni Persistence: `POST /extraer` solo funciona si las `*_URL` de `.env` apuntan a servicios accesibles. El sistema completo se levanta con `docker-compose.integration.yml` (ver [Integración con Docker Compose](#integración-con-docker-compose)).

## Integración con Docker Compose

`docker-compose.integration.yml` levanta el sistema completo:

```
host :8080 → traefik (web :80) → orchestrator → traefik (interno :8081) → validator / extractor / persistence → mongo
             └────────── red edge ──────────┘└───────────────────── red backend (internal) ─────────────────────┘
```

| Servicio | Origen | Red | Healthcheck |
|----------|--------|-----|-------------|
| `traefik` | `traefik:v3.6` | `edge` y `backend` | — (lo verifica la prueba de humo con `GET /health`) |
| `consul` | `hashicorp/consul:1.20` | `backend` | `consul members` |
| `orchestrator` | este repositorio (`build: .`) | `edge` y `backend` | `GET /health` |
| `validator` | `pdf-validator-service` @ `609f2e47a9ddf1e68a9a148984679361ad7eabfe` | `backend` | `GET /health` |
| `extractor` | `pdf-extract-service` @ `fbb9069067cd66060ae6f0820658ccb7ebe796d4` | `backend` | `GET /health` |
| `persistence` | `pdf-persistence-service` @ `11f59f5af8196328c743502616e6ce8eea4e57c5` | `backend` | `GET /openapi.json`: **solo el proceso** (ver abajo) |
| `mongo` | `mongo:8.0` | `backend` | `db.adminCommand('ping')` |

- **Solo Traefik publica un puerto** (`PUBLIC_PORT`, por defecto `8080`, hacia su entrypoint `web`). La red `backend` es `internal`: los servicios internos, Consul y MongoDB no son accesibles desde el host ni tienen salida a internet.
- **Providers de Traefik**:
  - **Docker Provider** (`--providers.docker=true`, `exposedbydefault=false`): descubre únicamente al `orchestrator` en el entrypoint público `web` (`PathPrefix(/)`), que así responde también los 404 en RFC 9457.
  - **Consul Catalog Provider** (`--providers.consulcatalog=true`, `endpoint.address=consul:8500`, `exposedbydefault=false`, `refreshinterval=500ms`): descubre dinámicamente a `validator`, `extractor` y `persistence` cuando están en estado `passing` y tienen `traefik.enable=true`.
  - **File Provider** (`--providers.file.filename=/etc/traefik/dinamico.yml`): define el `serversTransport` compartido `interno` con `responseHeaderTimeout: 8s`.
- **Registro dinámico con Consul**: `consul` corre como servidor en la red `backend` (`agent -dev -client=0.0.0.0`, sin publicar puertos ni cargar registros estáticos). `validator`, `extractor` y `persistence` dependen de `consul` (`service_healthy`) y se autorregistran al iniciar usando `CONSUL_URL=http://consul:8500` y `CONSUL_SERVICE_TAGS` (array JSON con los tags de Traefik). Cada servicio registra su propio healthcheck HTTP contra `/health` (intervalo `10s`, timeout `5s`, `DeregisterCriticalServiceAfter=1m`) y se desregistra al detenerse con `SIGTERM`. MongoDB no se registra en Consul porque es exclusivo de Persistence.
- **Flujo de comunicación interno**: `Cliente → Traefik público (:8080 → :80) → Orchestrator → Traefik interno (:8081) → Validator → Extractor → Persistence → MongoDB`. El Orchestrator solo coordina por HTTP a través de `http://traefik:8081/<servicio>` y no consulta directamente a Consul.
- **Entrypoint interno de Traefik** (`interno`, puerto `8081`, sin publicar): por ahí el Orchestrator llama a los tres servicios descubiertos en Consul. Solo es alcanzable desde la red de Docker.
- **URLs internas por nombre de servicio**, nunca `localhost`: `http://traefik:8081/validator`, `http://traefik:8081/extractor`, `http://traefik:8081/persistence` y `mongodb://mongo:27017/?serverSelectionTimeoutMS=5000`. El `serverSelectionTimeoutMS` hace que Persistence falle en 5 s, antes de escribir, si MongoDB no responde; sin él pymongo espera 30 s, más que el timeout del Orchestrator.
- **Datos de integración aislados**: base `pdf_extractext_integracion` y volumen `pdf-integracion-mongo-data`.
- **Una réplica por servicio**, con límites pensados para una máquina de 4 núcleos y ~4 GB para Docker (en total ~4 CPU y ~2.7 GB de techo). El Compose del Extractor, con 5 réplicas y su propio Traefik, no se usa.
- `MAX_FILE_SIZE_MB=10` se declara en Validator y Extractor para que no se desalineen.

### Versiones de los otros servicios

Cada servicio se construye desde su repositorio fijado a un **SHA completo** de su `main`, que es inmutable: un clon local desactualizado no puede colarse. Esos SHA se verificaron contra lo que consume cada adapter (rutas, métodos, campo `file`, campos de entrada y salida, tipos y estados de error, `Retry-After`, variables y puerto). Para actualizar un servicio, se cambia su SHA en este archivo y se repite esa verificación.

Cada contexto se puede reemplazar por una ruta local con `VALIDATOR_CONTEXT`, `EXTRACTOR_CONTEXT` y `PERSISTENCE_CONTEXT`. En ese caso, quien la use debe asegurarse de que el clon esté en el SHA indicado.

- **Validator** (`609f2e47a9ddf1e68a9a148984679361ad7eabfe`), **Extractor** (`fbb9069067cd66060ae6f0820658ccb7ebe796d4`) y **Persistence** (`11f59f5af8196328c743502616e6ce8eea4e57c5`) incorporan autorregistro dinámico en Consul (`CONSUL_URL` y `CONSUL_SERVICE_TAGS` en formato JSON), healthcheck HTTP en `/health` y desregistro en `SIGTERM`, manteniendo sin cambios los contratos HTTP que consume el Orchestrator.

### Circuit breaker

Si un servicio deja de responder, Traefik **deja de mandarle pedidos por un tiempo** y responde `503` al instante, en lugar de dejar cada pedido esperando el timeout. Así la falla de un servicio no se propaga al Orchestrator (fallo en cascada) y el servicio se recupera sin recibir más carga.

Lo maneja **Traefik**, no el código Python, igual que en el Compose del Extractor. Se configura en `docker-compose.integration.yml`:

- **Entrypoint interno** `interno` (`--entrypoints.interno.address=:8081`), sin publicar en el host.
- **Por servicio**, mediante la variable `CONSUL_SERVICE_TAGS` (array JSON que cada servicio publica al autorregistrarse en Consul): un router `PathPrefix(/<servicio>)` solo en `interno`, un middleware `<servicio>-circuit-breaker` aplicado antes de `<servicio>-sin-prefijo` (que quita el prefijo para que el servicio reciba sus rutas originales) y el `serversTransport` `interno@file`. Traefik toma el puerto `8000` directamente del registro del servicio en Consul.
- **Timeout**: el `serversTransport` `interno` se define con un file provider cuyo contenido está inline en `configs` del mismo Compose, porque las labels/tags pueden referenciar un `serversTransport` pero no definirlo. No se usa `--serversTransport.forwardingTimeouts.responseHeaderTimeout` global, como en el Compose del Extractor, porque también cortaría el salto público Cliente → Traefik → Orchestrator antes de que el Orchestrator responda su Problem Details.

```
CERRADO ──más del 50% de errores de red──► ABIERTO ──10 s──► SEMIABIERTO ──10 s sin fallar──► CERRADO
         (502/504 en la ventana de 10 s)    (503 inmediato)   (deja pasar tráfico de a poco;
                                                               si falla, vuelve a ABIERTO)
```

| Parámetro | Valor | Opción de Traefik | Significado (Traefik v3.6) |
|-----------|-------|-------------------|----------------------------|
| Umbral | `NetworkErrorRatio() > 0.50` | `circuitbreaker.expression` | Proporción de respuestas `502` o `504` sobre el total, en una ventana móvil de 10 s (buckets de 1 s) fija en Traefik y sin mínimo de pedidos: un solo `504` en la ventana da 1.0 y abre el circuito. Se evalúa cada 100 ms (`checkPeriod` por defecto). |
| Abierto | `10s` | `circuitbreaker.fallbackDuration` | Tiempo en que responde `503` (texto plano, sin `Retry-After`) sin llamar al servicio. |
| Semiabierto | `10s` | `circuitbreaker.recoveryDuration` | Tiempo en que deja pasar una proporción creciente del tráfico antes de cerrarse. |
| Timeout | `8s` | `serversTransports.interno.forwardingTimeouts.responseHeaderTimeout` | Espera de los headers después de enviar el pedido completo; al vencer, `504`. |

- **Independientes**: cada servicio tiene su propio middleware y su propio router. Con el circuito del Validator abierto, Extractor y Persistence siguen respondiendo `200` (verificado en la prueba de humo).
- **Backpressure del Extractor**: su `503` intencional (cola llena, `application/problem+json` con `Retry-After`) no es un error de red y **no abre el circuito**. Se verificó con una ráfaga de 150 pedidos concurrentes a `traefik:8081/extractor/api/v1/extraer`: 105 respondieron `503` del Extractor (70 %, más que el umbral) y 45 `200`, sin ningún `504`. Inmediatamente después, `GET /extractor/health` respondió `200` en ~12 ms y Traefik no registró ningún cambio de estado del circuito.
- **Límite conocido**: un pedido que espere en la cola del Extractor más de 8 s recibe el `504` de Traefik, y ese sí cuenta. En la ráfaga anterior ningún pedido llegó a 8 s (el más lento tardó ~5 s); con PDFs más pesados podría pasar.
- **`stop` y `pause` no son lo mismo.** Con `docker compose stop`, el servicio recibe `SIGTERM`, se desregistra de Consul, Traefik deja de descubrirlo en `consulCatalog`, borra su ruta y responde `404` sin pasar por el circuit breaker. Con `docker compose pause`, el kernel sigue aceptando la conexión TCP, Traefik corta con `504` a los 8 s y el circuito se abre; si la pausa se prolonga más que el healthcheck HTTP de Consul (intervalo `10s`, timeout `5s`), Consul marca el servicio como `critical` y Traefik retira la ruta hasta que vuelva a `passing`. La label `traefik.docker.allownonrunning=true`, que según la documentación respondería `503` con el contenedor detenido, **se descartó**: en Traefik v3.6.25 respondió `500` (`unsupported protocol scheme ""`), que tampoco cuenta para el circuito.
- La traducción al cliente de estas respuestas está en [Respuestas de Traefik](#respuestas-de-traefik-y-su-traducción).

**Cómo comprobarlo** con el stack levantado (ejemplo con el Validator; vale igual para `extractor` y `persistence`). La sonda hace un `GET` desde el contenedor del Orchestrator, porque el entrypoint interno no está publicado:

```bash
C="docker compose -f docker-compose.integration.yml"
sonda() { $C exec -T orchestrator python -c "import sys,time,urllib.request as u,urllib.error as e
t=time.monotonic()
try: s=u.urlopen(sys.argv[1],timeout=15).status
except e.HTTPError as x: s=x.code
print(s, round(time.monotonic()-t,3))" "http://traefik:8081/$1/openapi.json"; }

$C pause validator
sonda validator     # 504 en ~8 s: abre el circuito
sonda validator     # 503 en milisegundos: circuito abierto
sonda extractor     # 200: los otros circuitos siguen cerrados
$C unpause validator
sonda validator     # repetir cada segundo: 503 durante la ventana de 10 s, luego 200
$C logs traefik | grep validator-circuit-breaker   # estados tripped y recovering
```

En Git Bash sobre Windows, anteponer `MSYS_NO_PATHCONV=1` si alguna ruta se convierte. Medición real (prueba de humo): `504` en 8 s, `503` en menos de 1 s y `200` entre 9 y 35 s después de abrirse el circuito.

`tests/unit/test_compose_integracion.py` fija esta configuración y `test_circuit_breaker_se_abre_rechaza_sin_escribir_y_se_recupera` la comprueba contra el stack real.

### Disponibilidad de Persistence frente a conectividad con MongoDB

Persistence **no tiene un endpoint `/health`**. Su healthcheck consulta `/openapi.json`, que FastAPI expone por defecto: **solo indica que el proceso responde, no que pueda leer o escribir en MongoDB**. La disponibilidad de MongoDB la comprueba su propio healthcheck (`ping`), y la conectividad real de Persistence con la base la comprueba la prueba de humo:

- `GET /extractions/<id inexistente>` → `404`: Persistence consultó MongoDB, sin escribir.
- `POST /extraer` → el documento existe en MongoDB y `GET /extractions/<id>` lo devuelve.

Un `/health` de Persistence que haga ping a MongoDB es una mejora recomendada para su repositorio.

### Requisitos

Docker Desktop (backend WSL2 en Windows) con Docker Compose v2 y BuildKit. Para verificarlo:

```powershell
docker version            # debe mostrar Client y Server
docker compose version    # v2.x
docker info --format '{{.OSType}} CPUs={{.NCPU}} RAM={{.MemTotal}}'
```

Los contextos Git fijados a un SHA completo están documentados para BuildKit. Antes de levantar el stack, conviene confirmarlo con un solo servicio:

```bash
docker compose -f docker-compose.integration.yml config --quiet
docker compose -f docker-compose.integration.yml build validator
```

### Levantar y probar

```bash
docker compose -f docker-compose.integration.yml up --build -d --wait
E2E_BASE_URL=http://localhost:8080 \
E2E_PDF=../pdf-extract-service/tests/stress/pdfs/2020-Scrum-Guide-Spanish-Latin-South-American.pdf \
E2E_PAGINAS=16 \
uv run pytest -m e2e -v
```

| Test | Qué comprueba | ¿Escribe en MongoDB? |
|------|---------------|----------------------|
| `test_health_a_traves_de_traefik` | `GET /health` a través de Traefik | No |
| `test_solo_traefik_publica_puertos` | Ningún otro servicio publica puertos | No |
| `test_cada_servicio_expone_la_ruta_post_que_consume_el_orchestrator` | `/openapi.json` de cada servicio incluye `POST /validate`, `POST /api/v1/extraer` y `POST /extractions` | No |
| `test_persistence_consulta_mongodb_sin_escribir` | `GET /extractions/smoke-inexistente` → `404`, conteo sin cambios | No |
| `test_flujo_completo_persiste_y_recupera` | Respuesta pública, documento en MongoDB (`file_name`, `page_count`, largo del texto) y `GET /extractions/<id>` | **Sí: 1 documento** |
| `test_txt_rechazado_por_el_validator_sin_escribir` | `.txt` → `415` `/errors/unsupported-file-type`, conteo sin cambios | No |
| `test_con_persistence_detenido_traefik_responde_404_y_el_orchestrator_502_sin_escribir` | `docker compose stop persistence` → Traefik `404` en la ruta interna y `502` al cliente, conteo sin cambios; luego `start persistence` | No |
| `test_con_validator_pausado_traefik_corta_a_los_8s_y_el_orchestrator_responde_502_sin_escribir` | `docker compose pause validator` → `502` entre 8 y 10 s (cortó Traefik, no el Orchestrator), conteo sin cambios; luego `unpause validator` y espera a que su circuito se cierre | No |
| `test_traefik_publica_solo_el_entrypoint_web` | Traefik publica solo el puerto `80`; el entrypoint interno no | No |
| `test_cada_servicio_responde_a_traves_del_entrypoint_interno` | `traefik:8081/<servicio>/openapi.json` llega a cada servicio, sin el prefijo | No |
| `test_consul_registra_solo_los_tres_servicios_saludables` | Consul registra únicamente `validator`, `extractor` y `persistence` en `:8000` con estado `passing` | No |
| `test_circuit_breaker_se_abre_rechaza_sin_escribir_y_se_recupera` | Por servicio, con `pause`: `504` de Traefik entre 8 y 10 s, luego `503` en menos de 1 s, los otros dos circuitos en `200`, el cliente rechazado sin esperar un timeout (`503` Validator/Extractor, `502` Persistence), conteo sin cambios y `200` de nuevo entre 9 y 35 s | No |

Cada corrida escribe **exactamente un documento** y tarda ~4.5 min, casi todo esperando que los circuitos se recuperen. Las consultas internas se hacen desde el contenedor del Orchestrator con `docker compose exec`, sin publicar puertos. El circuito de Persistence se abre con un `GET` de solo lectura: un `POST` quedaría encolado en el contenedor pausado y podría escribir al reanudarlo.

### Baseline de rendimiento (k6) — Día 4

El escenario de k6 contra `POST /extraer` se encuentra en `tests/stress/k6-baseline.js` y el detalle completo de condiciones, comandos, percentiles de latencia y uso de CPU/RAM está en [`tests/stress/results/BASELINE.md`](./tests/stress/results/BASELINE.md):

| Escenario | VUs / Duración | Requests | Throughput | `avg` | `p50` | `p90` | `p95` | `p99` | % Errores |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baja concurrencia | `2 VUs` / `15s` | `146` | `9.66 req/s` | `205.40 ms` | `162.00 ms` | `295.22 ms` | `356.17 ms` | `1.06 s` | `0.00 %` |
| Concurrencia media | `10 VUs` / `15s` | `178` | `11.46 req/s` | `855.83 ms` | `766.30 ms` | `1.11 s` | `1.50 s` | `1.72 s` | `0.00 %` |

**Consumo de recursos observado (`docker stats` en régimen):**

- **Extractor:** ~`95 %` de CPU en baja concurrencia (`2 VUs`) y ~`97 %` en concurrencia media (`10 VUs`), con ~`102–107 MiB` de RAM.
- **MongoDB:** ~`4 %` de CPU en baja concurrencia (`2 VUs`) y ~`101 %` en concurrencia media (`10 VUs`), con ~`369–536 MiB` de RAM.
- **Orchestrator:** ~`27 %` y ~`22 %` de CPU respectivamente (~`50–56 MiB` de RAM).
- **Resto de los servicios** (`traefik`, `validator`, `consul`, `persistence`): consumos inferiores (~`4 %` a ~`16 %` de CPU).

### Evaluación de caché y optimización — Día 5

A partir de las mediciones de k6 y `docker stats`, se evaluó la incorporación de **Redis** o **Dragonfly** como caché:

1. **Dónde está el consumo:** el mayor consumo de CPU y memoria corresponde principalmente al **Extractor** (~`95 %`–`97 %` de CPU extrayendo texto del PDF) y a **MongoDB** (~`101 %` de CPU en concurrencia media), **no al Orchestrator** (~`22 %`–`27 %` de CPU).
2. **Ausencia de caso de uso concreto en el Orchestrator:** el Orchestrator solo coordina `Validator` → `Extractor` → `Persistence` y cada `POST /extraer` debe persistir una nueva extracción y devolver su `id` único generado por Persistence.
3. **Conclusión:** **no se justifica implementar caché (ni Redis ni Dragonfly) por ahora**, ya que no se identificó un caso concreto que demuestre su necesidad o beneficio y sumaría infraestructura innecesaria (ver fila `YAGNI` en [Revisión de principios](#revisión-de-principios-solid-kiss-dry-yagni)).

### Concurrencia, cuellos de botella y escalabilidad — Día 6

1. **Comparación de `2 VUs` vs. `10 VUs` (`15 s`):**
   - Al quintuplicar los usuarios concurrentes, el throughput pasó de `9.66 req/s` (`146` requests) a `11.46 req/s` (`178` requests), mientras que el tiempo de respuesta promedio subió de `205.40 ms` a `855.83 ms` y el percentil `p95` de `356.17 ms` a `1.50 s`, con **`0 %` de errores** en ambos escenarios.
2. **Cuellos de botella observados:**
   - **Extractor (`1.0 CPU`, 1 instancia, `EXTRACTION_CONCURRENCY=1`):** opera al ~`95 %`–`97 %` de CPU en ambos escenarios y encola los pedidos concurrentes (`EXTRACTION_QUEUE_SIZE=20`), fijando el techo de throughput de una sola instancia en ~`11.5 req/s` y elevando la latencia de espera en cola.
   - **MongoDB (`1.0 CPU`):** alcanza ~`101 %` de CPU en concurrencia media (`10 VUs`).
   - **Orchestrator:** **no aparece como el principal cuello de botella** en los escenarios probados (~`27 %` y ~`22 %` de CPU, ~`50–56 MiB` de RAM).
3. **Evaluación de routing y descubrimiento:**
   - La arquitectura de routing público e interno mediante **Traefik (`v3.6`)** (con Circuit Breakers independientes por servicio) y el descubrimiento de servicios mediante **Consul (`hashicorp/consul:1.20`)** (`consulCatalog`) funcionaron sin errores y con bajo consumo de CPU (~`14–16 %` y ~`4–6 %`).
4. **Alcance y pruebas pendientes:**
   - Estas conclusiones se limitan exclusivamente a los escenarios de `2 VUs` y `10 VUs` efectivamente probados; **no se afirma que el sistema esté optimizado para cualquier nivel de concurrencia**.
   - Quedan **pendientes** las pruebas adicionales de escalabilidad (como aumentar instancias/réplicas o workers y medir su impacto).

### Verificación final — Día 7

- **Suite unitaria y API (`pytest tests/unit tests/api`):** `216 passed`, `0 failed`.
- **Suite E2E (`pytest -m e2e -v`):** `18 passed` (`12` funciones de prueba, `0 failed`, escribiendo `1` único documento por corrida en `pdf_extractext_integracion.extractions`).
- **Calidad y formato (`ruff check` / `ruff format --check`):** `0` errores en los `37` archivos del proyecto.
- **Comparación con la baseline de carga:** las pruebas de k6 del Día 4 (`2 VUs` y `10 VUs` durante `15 s`) ya fueron ejecutadas sobre la configuración final actual (con `uv 0.13.7`, `certifi 2026.10.19`, sin `pip`, con Consul, Traefik `v3.6` y los tres Circuit Breakers). Al no haber cambios posteriores en el código ni en la infraestructura, la baseline representa directamente la configuración final y no se repitieron corridas de carga para evitar escrituras redundantes en MongoDB.
- **Redis / Dragonfly:** no aplicable (descartado en el Día 5; sin dependencias ni referencias en el código ni en los archivos de configuración).
- **Revisión de logs:** `0` excepciones no controladas ni errores de comunicación reales en los 7 contenedores; los registros `415`/`502`/`503` del Orchestrator y `WRN CircuitBreaker(state=tripped/recovering)` de Traefik provienen exclusivamente de los casos negativos y de resiliencia de las pruebas E2E.

### Detener

```bash
docker compose -f docker-compose.integration.yml down       # conserva el volumen y los datos
```

`down -v` borra el volumen `pdf-integracion-mongo-data` con todas las extracciones de integración.

## Pendiente

El seguimiento detallado por día se encuentra en [`TASKS.md`](./TASKS.md).

- ~~**Ejecutar la integración en una máquina con Docker**~~ (completado): se levantó el entorno de `docker-compose.integration.yml` y pasaron las 12 funciones de prueba E2E (`18` casos en total, incluyendo Consul y los tres Circuit Breakers).
- ~~**Confirmar el timeout** de 10 s con el sistema real~~ (completado): se verificó contra el stack real que, con el Validator sin responder, el Orchestrator devolvía `504` entre 10 y 15 s sin escribir en MongoDB. Con el circuit breaker, Traefik corta antes, a los 8 s (ver [Timeout](#timeout)). No se midieron PDFs más pesados.
- ~~**Circuit breaker y descubrimiento con Consul**~~ (completado): tres circuit breakers independientes en Traefik con servicios registrados y descubiertos en Consul, verificados contra el stack real (ver [Circuit breaker](#circuit-breaker)).
- ~~**Baseline de rendimiento (Día 4) y evaluación de caché (Día 5)**~~ (completado): mediciones con k6 (`2 VUs` y `10 VUs`) documentadas en [`tests/stress/results/BASELINE.md`](./tests/stress/results/BASELINE.md); la implementación de caché (Redis / Dragonfly) se descartó por ahora al no estar justificada.
- **Pruebas adicionales de escalabilidad (Día 6)**: aumentar instancias/réplicas o workers (especialmente del Extractor) y evaluar límites de recursos de MongoDB para medir su impacto bajo mayor concurrencia.
- **Traducir las respuestas de Traefik en los adapters** (requiere cambiar código Python; no se hizo para mantener el circuit breaker solo en infraestructura): hoy el `504` de Traefik llega al cliente como `502` en lugar de `504`, el `503` del circuito de Persistence como `502` en lugar de `503`, y Persistence detenido como `502` en lugar del `503` que daba antes de Traefik (ver [Respuestas de Traefik](#respuestas-de-traefik-y-su-traducción)).
- **Medir la cola del Extractor con PDFs pesados**: un pedido encolado más de 8 s recibe `504` de Traefik y cuenta para el circuit breaker.
