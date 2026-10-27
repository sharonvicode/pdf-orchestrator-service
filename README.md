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
| **KISS** | Un `httpx.AsyncClient` por servicio, abierto en el `lifespan`; sin reintentos, colas ni circuit breaker; los fallos se reducen a `502`, `503`, `504` y los rechazos propagados. | Un único `HTTP_TIMEOUT_SECONDS` para los tres servicios, aplicado por fase y no como límite total. |
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
│   ├── unit/                   # Caso de uso, adapters, configuración y entrypoint
│   ├── e2e/                    # Prueba de humo contra el stack de integración (Docker)
│   └── stubs.py                # Dobles de prueba auxiliares
├── Dockerfile
├── docker-compose.yml          # Solo el Orchestrator (ver sección Docker)
├── docker-compose.integration.yml  # Sistema completo con Traefik y MongoDB
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

**Sin reintentos.** El alta no es idempotente: un segundo `POST` crearía otra extracción. Si el timeout vence después de que Persistence guardó, el cliente recibe `504` aunque la extracción exista; **si el cliente reintenta, se crea un duplicado**. Un cliente que reintente tras un `504` debe asumir ese riesgo.

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
- `main.run()` construye `Settings` al arrancar el servidor: si falta una variable obligatoria o hay un valor inválido, el proceso falla **antes** de levantar. Usa `PORT` y `LOG_LEVEL`.
- El `lifespan` de FastAPI vuelve a leer `Settings` y abre un `httpx.AsyncClient` compartido por servicio, con su URL como `base_url` y `HTTP_TIMEOUT_SECONDS` como timeout. Los cierra al apagar. Si la configuración es inválida, la app no arranca.
- Importar la app (`main:app`) no lee la configuración: los tests que no ejecutan el lifespan no necesitan `.env`.

### Timeout

httpx aplica `HTTP_TIMEOUT_SECONDS` **a cada fase** de la llamada (conexión, envío, espera de respuesta, pool), no como límite total.

Se mantiene en **10 s**, según lo medido en el Extractor (README y `TASK/tp-test-de-carga.md` de su repositorio):

- En reposo, una extracción de los PDFs oficiales tarda entre **41 y 147 ms** (hasta 90 páginas y 8.5 MB). 10 s son ~70 veces el peor caso.
- Cada proceso del Extractor extrae de a uno y encola hasta 20 pedidos; con la cola llena responde `503` al instante. Una cola llena de PDFs como esos se vacía en ~3 s, dentro del timeout.
- Bajo sobrecarga sostenida (50 req/s) la mediana llegó a ~14.7 s. Ahí subir el timeout no ayuda: el pedido sigue esperando en una cola saturada, y el `504` libera antes al cliente.

Revisar el valor con las pruebas de punta a punta (PDFs más pesados por CPU, como uno de 300 páginas, no tienen medición individual). Un servicio puede terminar su trabajo aunque el Orchestrator ya haya respondido `504`; para Persistence eso implica el riesgo de duplicados descrito en su contrato.

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

`tests/e2e` es la prueba de humo contra el sistema real (ver [Integración con Docker Compose](#integración-con-docker-compose)); sin sus variables de entorno se salta.

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

**`docker-compose.yml`** levanta **solo el Orchestrator**, para desarrollo local y como smoke test de la imagen (`cp .env.example .env && docker compose up --build`). No incluye a Validator, Extractor ni Persistence: `POST /extraer` solo funciona si las `*_URL` de `.env` apuntan a servicios accesibles. El sistema completo se levanta con `docker-compose.integration.yml` (ver [Integración con Docker Compose](#integración-con-docker-compose)).

## Integración con Docker Compose

`docker-compose.integration.yml` levanta el sistema completo:

```
host :8080 → traefik → orchestrator → validator / extractor / persistence → mongo
             └──── red edge ────┘└──────────── red backend (internal) ───────────┘
```

| Servicio | Origen | Red | Healthcheck |
|----------|--------|-----|-------------|
| `traefik` | `traefik:v3.6` | `edge` | — (lo verifica la prueba de humo con `GET /health`) |
| `orchestrator` | este repositorio (`build: .`) | `edge` y `backend` | `GET /health` |
| `validator` | `pdf-validator-service` @ `79e22ae3e734d05567c50805ac0eab33d3cbdc5b` | `backend` | `GET /health` |
| `extractor` | `pdf-extract-service` @ `4166ba994cdebf6102f3608774b643bb751d2a4c` | `backend` | `GET /health` |
| `persistence` | `pdf-persistence-service` @ `95acae239467d8de2b550d1944543536e5d29730` | `backend` | `GET /openapi.json`: **solo el proceso** (ver abajo) |
| `mongo` | `mongo:8.0` | `backend` | `db.adminCommand('ping')` |

- **Solo Traefik publica un puerto** (`PUBLIC_PORT`, por defecto `8080`). Traefik usa el provider Docker con `exposedbydefault=false` y enruta `PathPrefix(/)` únicamente al Orchestrator, que así responde también los 404 en RFC 9457. La red `backend` es `internal`: los servicios internos no son accesibles desde el host ni tienen salida a internet.
- **URLs internas por nombre de servicio**, nunca `localhost`: `http://validator:8000`, `http://extractor:8000`, `http://persistence:8000` y `mongodb://mongo:27017/?serverSelectionTimeoutMS=5000`. El `serverSelectionTimeoutMS` hace que Persistence falle en 5 s, antes de escribir, si MongoDB no responde; sin él pymongo espera 30 s, más que el timeout del Orchestrator.
- **Datos de integración aislados**: base `pdf_extractext_integracion` y volumen `pdf-integracion-mongo-data`.
- **Una réplica por servicio**, con límites pensados para una máquina de 4 núcleos y ~4 GB para Docker (en total ~4 CPU y ~2.7 GB de techo). El Compose del Extractor, con 5 réplicas y su propio Traefik, no se usa.
- `MAX_FILE_SIZE_MB=10` se declara en Validator y Extractor para que no se desalineen.

### Versiones de los otros servicios

Cada servicio se construye desde su repositorio fijado a un **SHA completo** de su `main`, que es inmutable: un clon local desactualizado no puede colarse. Esos SHA se verificaron contra lo que consume cada adapter (rutas, métodos, campo `file`, campos de entrada y salida, tipos y estados de error, `Retry-After`, variables y puerto). Para actualizar un servicio, se cambia su SHA en este archivo y se repite esa verificación.

Cada contexto se puede reemplazar por una ruta local con `VALIDATOR_CONTEXT`, `EXTRACTOR_CONTEXT` y `PERSISTENCE_CONTEXT`. En ese caso, quien la use debe asegurarse de que el clon esté en el SHA indicado.

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
| `test_con_persistence_detenido_responde_503_sin_escribir` | `docker compose stop persistence` → `503`, conteo sin cambios; luego `start persistence` | No |
| `test_con_validator_pausado_responde_504_al_vencer_el_timeout_sin_escribir` | `docker compose pause validator` → `504` entre 10 y 15 s (timeout real), conteo sin cambios; luego `unpause validator` | No |

Cada corrida escribe **exactamente un documento**. Las consultas internas a Persistence se hacen desde el contenedor del Orchestrator con `docker compose exec`, sin publicar puertos.

### Detener

```bash
docker compose -f docker-compose.integration.yml down       # conserva el volumen y los datos
```

`down -v` borra el volumen `pdf-integracion-mongo-data` con todas las extracciones de integración.

## Pendiente

- ~~**Ejecutar la integración en una máquina con Docker**~~ (completado): se levantó el entorno de `docker-compose.integration.yml` y las 10 pruebas E2E pasaron.
- ~~**Confirmar el timeout** de 10 s con el sistema real~~ (completado): `test_con_validator_pausado_responde_504_al_vencer_el_timeout_sin_escribir` verificó contra el stack real que, con el Validator sin responder, el Orchestrator devuelve `504` entre 10 y 15 s sin escribir en MongoDB. No se midieron PDFs más pesados (ver [Timeout](#timeout)).
