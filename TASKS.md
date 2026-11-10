# TASKS — PDF ExtractText Orchestrator

Registro del estado de avance del proyecto **PDF Orchestrator Service**, alineado con [`README.md`](./README.md) y [`tests/stress/results/BASELINE.md`](./tests/stress/results/BASELINE.md).

---

## Resumen de estado por día

| Día | Etapa | Estado |
|-----|-------|--------|
| **Día 1** | Arquitectura Ports & Adapters, contratos internos, errores RFC 9457, configuración y Docker base | Completado |
| **Día 2** | Integración HTTP con Validator, Extractor y Persistence (`httpx`) y suite de pruebas | Completado |
| **Día 3** | Integración con Docker Compose, Traefik, Circuit Breaker, Consul y correcciones de seguridad | Completado |
| **Día 4** | Baseline de rendimiento con k6 (baja y media concurrencia) y medición de recursos | Completado |
| **Día 5** | Análisis de rendimiento y evaluación de caché (Redis / Dragonfly) | Completado (implementación de caché no justificada por ahora) |
| **Día 6** | Análisis de concurrencia, cuellos de botella y escalabilidad | Parcial: análisis y detección de cuellos de botella realizados; pruebas adicionales de escalabilidad pendientes |
| **Día 7** | Preparación final, verificación integral de pruebas/logs/configuración y cierre documental | Completado |

---

## Día 1 – Diseño, arquitectura y estructura base

- [x] Definir la responsabilidad del Orchestrator como coordinador HTTP (`Validator` → `Extractor` → `Persistence`).
- [x] Implementar arquitectura **Ports & Adapters** (`app/ports.py`, `app/services/orchestrator.py`, `app/dependencies.py`).
- [x] Definir endpoints públicos `POST /extraer` y `GET /health` y DTOs (`app/routes.py`, `app/schemas.py`).
- [x] Estandarizar el manejo de errores en formato **RFC 9457 Problem Details** (`application/problem+json`).
- [x] Centralizar la configuración mediante variables de entorno con `pydantic-settings` (`app/config.py`).
- [x] Crear `Dockerfile` (usuario no-root `appuser`, `uv sync --frozen --no-dev`), `docker-compose.yml` standalone y revisión de principios (`SOLID`, `KISS`, `DRY`, `YAGNI`).

---

## Día 2 – Integración HTTP de microservicios

- [x] Implementar adapters HTTP (`HttpValidator`, `HttpExtractor`, `HttpPersistence`) sobre clientes `httpx.AsyncClient` compartidos en el `lifespan`.
- [x] Traducir respuestas de error y fallos de comunicación (`400`, `413`, `415`, `422`, `502`, `503` con `Retry-After` y `504`).
- [x] Cubrir casos unitarios y de flujo HTTP completo con `httpx.MockTransport` (`tests/unit/` y `tests/api/`).

---

## Día 3 – Entorno de integración, resiliencia, descubrimiento y seguridad

- [x] Configurar `docker-compose.integration.yml` con redes aisladas (`edge` pública solo en Traefik y `backend` interna) y MongoDB exclusivo para Persistence.
- [x] Configurar **Traefik v3.6** como reverse proxy público (`:8080`) e interno (`:8081`) con **Circuit Breaker independiente** para `validator`, `extractor` y `persistence` (`NetworkErrorRatio() > 0.50`, `fallbackDuration=10s`, `recoveryDuration=10s`, `responseHeaderTimeout=8s`).
- [x] Incorporar **Consul** (`hashicorp/consul:1.20`) para el registro y descubrimiento de `validator`, `extractor` y `persistence`, integrado con Traefik mediante `consulCatalog` sin perder los tres Circuit Breakers.
- [x] Aplicar correcciones de seguridad en la imagen del Orchestrator: actualización de `uv` (`0.13.7`), actualización de `certifi` (`2026.10.19`) y eliminación de `pip` de la imagen final.
- [x] Verificar con pruebas unitarias/API (`216 passed`) y pruebas E2E contra el stack real (`12` funciones de prueba / `18` casos ejecutados en `tests/e2e/test_smoke.py`).

---

## Día 4 – Baseline de rendimiento con k6

- [x] Crear el escenario [`tests/stress/k6-baseline.js`](./tests/stress/k6-baseline.js) probando `POST /extraer` con el PDF `2020-Scrum-Guide-Spanish-Latin-South-American.pdf` (16 páginas, ~312 KB).
- [x] Ejecutar escenario de **baja concurrencia** (`2 VUs` durante `15 s`): `146` requests, `9.66 req/s`, promedio `205.40 ms`, `p50` `162.00 ms`, `p90` `295.22 ms`, `p95` `356.17 ms`, `p99` `1.06 s`, `0.00 %` de errores.
- [x] Ejecutar escenario de **concurrencia media** (`10 VUs` durante `15 s`): `178` requests, `11.46 req/s`, promedio `855.83 ms`, `p50` `766.30 ms`, `p90` `1.11 s`, `p95` `1.50 s`, `p99` `1.72 s`, `0.00 %` de errores.
- [x] Registrar consumo de CPU y RAM por contenedor y documentar resultados en [`tests/stress/results/BASELINE.md`](./tests/stress/results/BASELINE.md).

---

## Día 5 – Caché y optimización

**Estado:** Análisis de rendimiento y evaluación de caché completados. Implementación de caché no justificada por ahora.

### Tareas realizadas
- [x] Revisar los resultados de k6 y el consumo de recursos obtenidos en el baseline.
- [x] Identificar los componentes con mayor consumo de recursos durante las pruebas.
- [x] Evaluar la incorporación de **Redis** o **Dragonfly** como capa de caché.

### Análisis y conclusión
1. **Consumo de recursos observado:**
   - **Extractor:** ~`95 %` de CPU en baja concurrencia (`2 VUs`) y ~`97 %` de CPU en concurrencia media (`10 VUs`), con ~`102–107 MiB` de RAM.
   - **MongoDB:** ~`4 %` de CPU en baja concurrencia (`2 VUs`) y ~`101 %` de CPU en concurrencia media (`10 VUs`), con ~`369–536 MiB` de RAM.
   - **Orchestrator:** ~`27 %` de CPU en baja concurrencia y ~`22 %` en concurrencia media, con ~`50–56 MiB` de RAM.
   - Los demás servicios (`traefik`, `validator`, `consul`, `persistence`) registraron consumos inferiores (entre ~`4 %` y ~`16 %` de CPU).
2. **Evaluación de Redis / Dragonfly:**
   - El mayor consumo de recursos se concentra principalmente en el **Extractor** (procesamiento intensivo de CPU al extraer texto del PDF) y en **MongoDB** (escrituras y manejo de documentos bajo concurrencia media), **no en el Orchestrator**.
   - El Orchestrator solo coordina las llamadas HTTP (`Validator` → `Extractor` → `Persistence`) y no mantiene estado ni realiza cómputo pesado. Además, cada llamada a `POST /extraer` requiere persistir una nueva extracción y devolver su `id` generado por Persistence, sin que exista hoy un caso de consulta repetida por clave o de reutilización de respuestas en el Orchestrator.
   - **Conclusión:** **No se justifica implementar caché (ni Redis ni Dragonfly) por ahora**, ya que no se identificó un caso concreto que demuestre su necesidad o beneficio y agregaría infraestructura y complejidad innecesarias (principio **YAGNI**).

---

## Día 6 – Concurrencia y escalabilidad

**Estado:** Análisis de concurrencia y detección de cuellos de botella realizados sobre los escenarios probados. Pruebas adicionales de escalabilidad pendientes.

### Tareas realizadas y pendientes
- [x] Comparar los resultados de los escenarios de `2 VUs` (baja concurrencia) y `10 VUs` (concurrencia media).
- [x] Identificar los cuellos de botella observados en el sistema integrado.
- [x] Verificar el comportamiento del Orchestrator frente a los demás componentes en los escenarios probados.
- [x] Evaluar la arquitectura de routing mediante **Traefik** y el descubrimiento de servicios mediante **Consul**.
- [ ] **Pendiente:** Ejecutar pruebas adicionales de escalabilidad (por ejemplo, aumentar instancias/réplicas o workers de los servicios y medir su impacto bajo mayor carga).

### Análisis de concurrencia y cuellos de botella (escenarios de 2 y 10 VUs)
1. **Comparación entre 2 VUs y 10 VUs (durante 15 s):**
   - **Requests completados y throughput:** al multiplicar por 5 la concurrencia (`2 VUs` → `10 VUs`), el total de requests pasó de `146` a `178` y el throughput pasó de `9.66 req/s` a `11.46 req/s` (+`18.6 %`), mostrando que el rendimiento por segundo se ameseta alrededor de ~`11.5 req/s` con la topología actual de 1 instancia por servicio.
   - **Latencia:** el tiempo promedio subió de `205.40 ms` a `855.83 ms` (~`4.17x`), la mediana (`p50`) de `162.00 ms` a `766.30 ms` y el percentil `p95` de `356.17 ms` a `1.50 s`.
   - **Tasa de error:** `0.00 %` en ambos escenarios; ningún pedido superó el timeout de `8 s` de Traefik ni abrió los Circuit Breakers.
2. **Cuellos de botella observados:**
   - **Extractor (`1.0 CPU`, 1 instancia, `EXTRACTION_CONCURRENCY=1`):** opera prácticamente al tope de su CPU asignada tanto con `2 VUs` (~`95 %`) como con `10 VUs` (~`97 %`). Al procesar una extracción a la vez y encolar el resto (`EXTRACTION_QUEUE_SIZE=20`), los pedidos concurrentes esperan en cola, lo que explica el aumento de latencia mediana y en `p95` sin incremento proporcional del throughput.
   - **MongoDB (`1.0 CPU`):** pasa de ~`4 %` de CPU con `2 VUs` a ~`101 %` de CPU con `10 VUs` (y de `369.4 MiB` a `536.3 MiB` de RAM), convirtiéndose en el segundo componente que alcanza el límite de CPU asignado en concurrencia media.
3. **Rol del Orchestrator:**
   - El **Orchestrator no aparece como el principal cuello de botella** en los escenarios probados: su uso de CPU se mantuvo bajo (~`27 %` con `2 VUs` y ~`22 %` con `10 VUs`, sobre un límite de `0.5 CPU`) y su memoria estable (~`50–56 MiB` sobre `512 MiB`), dedicando la mayor parte del tiempo a esperar de forma asíncrona (`httpx.AsyncClient`) las respuestas de los servicios internos.
4. **Evaluación de routing (Traefik) y descubrimiento de servicios (Consul):**
   - **Traefik (`v3.6`)** enruta el tráfico público (`:8080`) hacia el Orchestrator y el tráfico interno (`:8081`) hacia `validator`, `extractor` y `persistence`, aplicando un **Circuit Breaker independiente** por servicio y un `responseHeaderTimeout` de `8 s`. Durante las pruebas de carga consumió ~`14–16 %` de CPU sin introducir errores ni cuellos de botella.
   - **Consul (`hashicorp/consul:1.20`)** registra `validator`, `extractor` y `persistence` con healthchecks TCP cada `1 s` y provee el catálogo a Traefik (`consulCatalog` con `refreshinterval=500ms`), manteniendo un consumo marginal (~`4–6 %` de CPU). Esta arquitectura desacopla al Orchestrator de las direcciones físicas de los servicios internos.
5. **Alcance y trabajo pendiente de escalabilidad:**
   - Las conclusiones anteriores **se limitan estrictamente a los escenarios de 2 y 10 VUs efectivamente probados** con 1 instancia por servicio; **no se afirma que el sistema esté optimizado para cualquier nivel de concurrencia**.
   - Quedan **pendientes** las pruebas adicionales de escalabilidad (como aumentar instancias/réplicas o workers del Extractor y evaluar los límites de recursos de MongoDB) para medir empíricamente su impacto en throughput y latencia.

---

## Día 7 – Preparación final y verificación integral

**Estado:** Completado.

### Estado de las 10 tareas del Día 7

1. **Ejecutar tests completos (Completado):**
   - [x] Inspeccionar la organización de `tests/unit` (caso de uso, adapters HTTP, configuración, entrypoint y contrato de `docker-compose.integration.yml`), `tests/api` (endpoints, RFC 9457 y flujo HTTP completo con `MockTransport`), `tests/e2e` (prueba de humo contra el stack real) y `tests/stress` (`k6-baseline.js`).
   - [x] Ejecutar suite completa de pruebas unitarias y API: **`216 passed`**, **`0 failed`** (`18 skipped` de E2E cuando se corre sin variables `E2E_*`).
   - [x] Ejecutar linter y formato (`ruff check` y `ruff format --check` sobre los 37 archivos de código y pruebas): **0 errores**.
2. **Ejecutar pruebas de integración (Completado):**
   - [x] Verificar el estado del stack de integración (`7` contenedores activos y `healthy`: `traefik`, `consul`, `orchestrator`, `validator`, `extractor`, `persistence`, `mongo`).
   - [x] Identificar pruebas que escriben en MongoDB antes de ejecutar: únicamente `test_flujo_completo_persiste_y_recupera` inserta `1` documento en `pdf_extractext_integracion.extractions`; las otras `11` funciones (`17` casos) no escriben documentos.
   - [x] Ejecutar una única corrida de `pytest -m e2e -v`: **`18 passed`** (`12` funciones de prueba, `0 failed`, en `167.11 s`), pasando el conteo de documentos en MongoDB de `335` a `336`.
3. **Ejecutar pruebas de carga (No se repite — baseline ya corresponde a la configuración final):**
   - [x] Revisar [`tests/stress/k6-baseline.js`](./tests/stress/k6-baseline.js) y verificar que las pruebas del Día 4 (`2 VUs` y `10 VUs` durante `15 s`) se ejecutaron sobre la configuración final actual (con `uv 0.13.7`, `certifi 2026.10.19`, sin `pip`, con Consul, Traefik `v3.6` y los tres Circuit Breakers).
   - No se repitieron las corridas de k6 para no generar escrituras innecesarias en MongoDB, dado que no hubo cambios posteriores en código, imágenes ni configuración.
4. **Comparar baseline contra configuración final (No aplica comparación separada):**
   - [x] Documentar que la baseline del Día 4 **ya representa la configuración final del sistema**, por lo que las métricas finales son exactamente las medidas en [`tests/stress/results/BASELINE.md`](./tests/stress/results/BASELINE.md) (`2 VUs`: `146` requests, `9.66 req/s`, `avg` `205.40 ms`, `p95` `356.17 ms`, `0 %` errores; `10 VUs`: `178` requests, `11.46 req/s`, `avg` `855.83 ms`, `p95` `1.50 s`, `0 %` errores).
5. **Verificar Traefik, Consul y Circuit Breakers (Completado):**
   - [x] Comprobar que Traefik publica únicamente el entrypoint `web` (`:8080` → `:80`) hacia el Orchestrator y mantiene el entrypoint `interno` (`:8081`) sin publicar al host.
   - [x] Comprobar que Consul (`hashicorp/consul:1.20` en la red interna `backend`) registra únicamente `validator`, `extractor` y `persistence` en `:8000` con checks TCP en estado `passing`, y que Traefik los descubre mediante `consulCatalog`.
   - [x] Comprobar el funcionamiento de los tres Circuit Breakers independientes (`NetworkErrorRatio() > 0.50`, `fallbackDuration=10s`, `recoveryDuration=10s`, `responseHeaderTimeout=8s`) y el aislamiento de los servicios internos y de MongoDB.
6. **Verificar Redis / Dragonfly (No aplicable — N/A):**
   - [x] Comprobar que no existe ninguna dependencia ni referencia accidental a Redis o Dragonfly en `pyproject.toml`, `uv.lock`, `app/`, `main.py`, `Dockerfile`, `docker-compose*.yml` ni `.env.example`.
   - Se deja constancia de que la tarea **no aplica** porque en la evaluación del Día 5 se decidió no incorporar caché.
7. **Revisar logs y errores (Completado):**
   - [x] Inspeccionar los logs de `traefik`, `consul`, `orchestrator`, `validator`, `extractor`, `persistence` y `mongo`.
   - [x] Confirmar **0 excepciones no controladas, 0 tracebacks y 0 errores de comunicación reales**: las respuestas `415`, `502` y `503` en `orchestrator` y los mensajes `WRN CircuitBreaker(state=tripped/recovering)` en `traefik` corresponden exclusivamente a las pruebas negativas y de apertura de circuito de la suite E2E.
8. **Eliminar configuraciones innecesarias (Completado):**
   - [x] Auditar `pyproject.toml`, `.env.example`, `.dockerignore`, `.gitignore`, `Dockerfile`, `docker-compose.yml`, `docker-compose.integration.yml` y `app/config.py`.
   - [x] Confirmar que no existen variables de entorno sin uso, servicios o dependencias sobrantes ni archivos temporales rastreados en el proyecto; todas las configuraciones presentes son utilizadas por el runtime o las suites de pruebas.
9. **Documentar configuración final (Completado):**
   - [x] Actualizar [`README.md`](./README.md), [`TASKS.md`](./TASKS.md) y [`tests/stress/results/BASELINE.md`](./tests/stress/results/BASELINE.md) con la configuración y resultados finales.
10. **Registrar resultados finales en el proyecto (Completado):**
    - [x] Consolidar el resumen final de cierre en este documento.

---

## Resumen final de entrega

- **Pruebas ejecutadas y resultados:**
  - **Unitarias y API (`pytest tests/unit tests/api`):** `216 passed`, `0 failed` (`4.19 s`).
  - **Integración y E2E (`pytest -m e2e -v`):** `18 passed` (`12` funciones de prueba, `0 failed`, `167.11 s`), verificando flujo completo con persistencia en MongoDB, rechazo de archivo inválido (`.txt` → `415`), caída/pausa de servicios, descubrimiento en Consul y apertura/recuperación independiente de los tres Circuit Breakers en Traefik.
  - **Calidad de código (`ruff check` y `ruff format --check`):** `0` errores en los `37` archivos del proyecto.
  - **Seguridad de imagen (`trivy image`):** `0` vulnerabilidades en los `19` paquetes Python (`/app/.venv`, con `certifi 2026.10.19`) y `0` vulnerabilidades en el binario `/bin/uv` (`0.13.7`), con `pip` desinstalado de la imagen final.
  - **Rendimiento (k6 sobre configuración final):** `2 VUs` (`15 s`) → `146` requests (`9.66 req/s`, `avg` `205.40 ms`, `p95` `356.17 ms`, `0 %` errores); `10 VUs` (`15 s`) → `178` requests (`11.46 req/s`, `avg` `855.83 ms`, `p95` `1.50 s`, `0 %` errores).
- **Tareas no aplicables:**
  - Implementación/verificación de **Redis o Dragonfly** (descartado en el Día 5 por principio YAGNI al no existir caso de uso en el Orchestrator y concentrarse el consumo de CPU en el Extractor y MongoDB).
  - Repetición de pruebas de carga y comparación independiente post-baseline (la baseline del Día 4 ya fue medida sobre la configuración final).
- **Limitaciones conocidas y tareas pendientes:**
  - **Escalabilidad bajo mayor concurrencia (Día 6):** las conclusiones de rendimiento se limitan a los escenarios probados (`2` y `10 VUs` con 1 instancia por servicio). Quedan pendientes las pruebas adicionales aumentando instancias/réplicas o workers del Extractor (~`95 %–97 %` de CPU con `EXTRACTION_CONCURRENCY=1`) y ajustando recursos de MongoDB (~`101 %` de CPU en `10 VUs`).
  - **Traducción de respuestas generadas por Traefik en los adapters:** un timeout de `8 s` de Traefik (`504`) o el circuito abierto de Persistence (`503`) llegan hoy al cliente como `502` porque los adapters no fueron modificados para distinguir las respuestas en texto plano de Traefik.
  - **Cola del Extractor con PDFs más pesados:** un pedido que permanezca más de `8 s` en la cola del Extractor recibiría `504` de Traefik y computaría para el Circuit Breaker.
  - **`app/adapters/fakes.py` en la imagen:** solo lo usan los tests unitarios/API, pero se copia dentro de `app/` para no alterar imports existentes.
