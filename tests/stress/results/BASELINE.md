# Baseline de Rendimiento — PDF ExtractText Orchestrator (k6)

Fecha de medición: `2026-11-10`

## 1. Condiciones de prueba

- **Endpoint probado:** `POST http://localhost:8080/extraer` (`multipart/form-data`, campo `file`).
- **Recorrido de cada request (punta a punta real):**
  `k6` → `Traefik (entrypoint web :8080)` → `Orchestrator (:8000)` → `Traefik (entrypoint interno :8081, descubrimiento vía Consul + Circuit Breaker)` → `Validator` → `Extractor` → `Persistence` → `MongoDB 8.0`.
- **Archivo PDF de prueba:** `2020-Scrum-Guide-Spanish-Latin-South-American.pdf` (`319,343 bytes` / ~312 KB, 16 páginas).
- **Topología y límites de recursos (`docker-compose.integration.yml`, 1 réplica por servicio):**
  - `traefik` (`traefik:v3.6`): `0.5 CPU`, `256 MB` RAM
  - `consul` (`hashicorp/consul:1.20`): `0.25 CPU`, `128 MB` RAM
  - `orchestrator`: `0.5 CPU`, `512 MB` RAM (`HTTP_TIMEOUT_SECONDS=10`)
  - `validator`: `0.5 CPU`, `384 MB` RAM (`MAX_FILE_SIZE_MB=10`)
  - `extractor`: `1.0 CPU`, `512 MB` RAM (`EXTRACTION_CONCURRENCY=1`, `EXTRACTION_QUEUE_SIZE=20`, `RETRY_AFTER_SECONDS=1`)
  - `persistence`: `0.5 CPU`, `256 MB` RAM
  - `mongo` (`mongo:8.0`): `1.0 CPU`, `768 MB` RAM
- **Herramienta:** `grafana/k6:latest` ejecutado con [`tests/stress/k6-baseline.js`](../k6-baseline.js).
- **Verificación de persistencia en MongoDB (`pdf_extractext_integracion.extractions`):**
  - Conteo previo: `11` documentos.
  - Tras baja concurrencia (+146 requests exitosos): `157` documentos.
  - Tras concurrencia media (+178 requests exitosos): `335` documentos.

---

## 2. Comandos utilizados

```powershell
# 1. Baja concurrencia (2 VUs constantes durante 15 s)
docker run --rm `
  -v "${PWD}/tests/stress:/scripts:ro" `
  -v "${PWD}/../pdf-extract-service/tests/stress/pdfs:/pdfs:ro" `
  -e BASE_URL=http://host.docker.internal:8080 `
  -e VUS=2 -e DURATION=15s `
  grafana/k6:latest run /scripts/k6-baseline.js

# 2. Concurrencia media (10 VUs constantes durante 15 s)
docker run --rm `
  -v "${PWD}/tests/stress:/scripts:ro" `
  -v "${PWD}/../pdf-extract-service/tests/stress/pdfs:/pdfs:ro" `
  -e BASE_URL=http://host.docker.internal:8080 `
  -e VUS=10 -e DURATION=15s `
  grafana/k6:latest run /scripts/k6-baseline.js

# Captura de CPU y RAM en régimen durante cada prueba
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"
```

---

## 3. Resultados de rendimiento (`http_req_duration` y throughput)

| Escenario | VUs | Duración | Requests totales | Throughput (`req/s`) | Promedio (`avg`) | `p50` (`med`) | `p90` | `p95` | `p99` | Máx (`max`) | % Errores (`http_req_failed`) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Baja concurrencia** ([`k6-baja-concurrencia.txt`](./k6-baja-concurrencia.txt)) | 2 | 15 s | 146 | **9.66 req/s** | 205.40 ms | 162.00 ms | 295.22 ms | 356.17 ms | 1.06 s | 1.17 s | **0.00 %** (0/146) |
| **Concurrencia media** ([`k6-concurrencia-media.txt`](./k6-concurrencia-media.txt)) | 10 | 15 s | 178 | **11.46 req/s** | 855.83 ms | 766.30 ms | 1.11 s | 1.50 s | 1.72 s | 1.93 s | **0.00 %** (0/178) |

- En ambos escenarios el `100 %` de los checks (`status 200` y `exito == true`) pasaron sin errores ni apertura de Circuit Breakers.
- Al pasar de `2 VUs` a `10 VUs`, el throughput sube de `9.66 req/s` a `11.46 req/s` (techo impuesto por la única réplica del Extractor con `1.0 CPU` y `EXTRACTION_CONCURRENCY=1`), mientras que la latencia mediana (`p50`) pasa de `162 ms` a `766.3 ms` por el encolado controlado en el Extractor (`EXTRACTION_QUEUE_SIZE=20`), manteniéndose muy por debajo del timeout de `8 s` de Traefik y de `10 s` del Orchestrator.

---

## 4. Uso de CPU y RAM por contenedor (en régimen)

| Contenedor | Límite CPU / RAM | CPU % (Baja: 2 VUs) | RAM (Baja: 2 VUs) | CPU % (Media: 10 VUs) | RAM (Media: 10 VUs) |
|---|---|---|---|---|---|
| `pdf-integracion-extractor-1` | `1.0 CPU` / `512 MiB` | `94.78 %` | `101.7 MiB` (`19.86 %`) | `97.42 %` | `106.7 MiB` (`20.85 %`) |
| `pdf-integracion-mongo-1` | `1.0 CPU` / `768 MiB` | `3.91 %` | `369.4 MiB` (`48.10 %`) | `100.92 %` | `536.3 MiB` (`69.83 %`) |
| `pdf-integracion-orchestrator-1` | `0.5 CPU` / `512 MiB` | `27.25 %` | `50.39 MiB` (`9.84 %`) | `22.41 %` | `56.00 MiB` (`10.94 %`) |
| `pdf-integracion-traefik-1` | `0.5 CPU` / `256 MiB` | `16.08 %` | `29.17 MiB` (`11.39 %`) | `13.73 %` | `31.98 MiB` (`12.49 %`) |
| `pdf-integracion-validator-1` | `0.5 CPU` / `384 MiB` | `15.64 %` | `62.89 MiB` (`16.38 %`) | `9.54 %` | `65.19 MiB` (`16.98 %`) |
| `pdf-integracion-consul-1` | `0.25 CPU` / `128 MiB` | `6.22 %` | `46.39 MiB` (`36.25 %`) | `4.47 %` | `44.82 MiB` (`35.02 %`) |
| `pdf-integracion-persistence-1` | `0.5 CPU` / `256 MiB` | `5.49 %` | `43.84 MiB` (`17.12 %`) | `5.19 %` | `43.78 MiB` (`17.10 %`) |

---

## 5. Evaluación de caché y optimización (Día 5)

1. **Componentes con mayor consumo de recursos:**
   - **Extractor:** ~`95 %` de CPU en baja concurrencia (`2 VUs`) y ~`97 %` en concurrencia media (`10 VUs`).
   - **MongoDB:** ~`4 %` de CPU en baja concurrencia (`2 VUs`) y ~`101 %` en concurrencia media (`10 VUs`).
   - **Orchestrator:** ~`27 %` y ~`22 %` de CPU respectivamente (~`50–56 MiB` de RAM); el resto de los servicios tuvo consumos inferiores (~`4–16 %` de CPU).
2. **Evaluación de incorporar Redis o Dragonfly como caché:**
   - El mayor consumo observado corresponde principalmente al **Extractor** y a **MongoDB**, **no al Orchestrator**.
   - El Orchestrator únicamente coordina el flujo HTTP (`Validator` → `Extractor` → `Persistence`) y cada `POST /extraer` requiere registrar una nueva extracción en Persistence y devolver su `id` único.
   - **Conclusión:** **No se justifica implementar caché (Redis ni Dragonfly) por ahora**, ya que no se identificó un caso concreto que demuestre su necesidad o beneficio en el Orchestrator (principio **YAGNI**).

---

## 6. Análisis de concurrencia, cuellos de botella y escalabilidad (Día 6)

1. **Comparación entre `2 VUs` y `10 VUs` (`15 s`):**
   - El throughput pasó de `9.66 req/s` (`146` requests) a `11.46 req/s` (`178` requests), mientras que el tiempo de respuesta promedio subió de `205.40 ms` a `855.83 ms` y el percentil `p95` de `356.17 ms` a `1.50 s`, con `0.00 %` de errores en ambos escenarios.
2. **Cuellos de botella identificados:**
   - **Extractor (`1.0 CPU`, 1 instancia, `EXTRACTION_CONCURRENCY=1`):** alcanza el ~`95 %`–`97 %` de CPU y encola los pedidos concurrentes (`EXTRACTION_QUEUE_SIZE=20`), limitando el throughput máximo de una sola instancia a ~`11.5 req/s` e incrementando la latencia de espera en cola.
   - **MongoDB (`1.0 CPU`):** alcanza ~`101 %` de CPU y ~`536.3 MiB` de RAM en concurrencia media (`10 VUs`).
   - **Orchestrator:** **no aparece como el principal cuello de botella** en los escenarios probados (~`27 %` y ~`22 %` de CPU, ~`50–56 MiB` de RAM).
3. **Arquitectura de routing y descubrimiento evaluada:**
   - Se verificó el funcionamiento conjunto de **Traefik (`v3.6`)** (routing público e interno con Circuit Breakers independientes por servicio) y **Consul (`hashicorp/consul:1.20`)** (registro y descubrimiento mediante `consulCatalog`), ambos con bajo consumo de CPU (~`14–16 %` y ~`4–6 %`).
4. **Alcance y pruebas pendientes:**
   - Estas conclusiones se limitan exclusivamente a los escenarios de `2 VUs` y `10 VUs` efectivamente probados con una instancia por servicio; **no se afirma que el sistema esté optimizado para cualquier nivel de concurrencia**.
   - Quedan **pendientes** las pruebas adicionales de escalabilidad (como aumentar instancias/réplicas o workers y medir su impacto).

---

## 7. Verificación de configuración final y comparación (Día 7)

- **Correspondencia con la configuración final:** Las pruebas de los escenarios de `2 VUs` y `10 VUs` registradas en este documento se ejecutaron con la arquitectura y configuración finales ya aplicadas (`uv 0.13.7`, `certifi 2026.10.19`, `pip` desinstalado, **Consul `1.20`**, **Traefik `v3.6`** con `consulCatalog` y los **tres Circuit Breakers** independientes).
- **Por qué no se repiten las pruebas de carga ni aplica una comparación separada:** Con posterioridad a esta baseline no se realizaron cambios en el código funcional de Python, en `Dockerfile`, en `docker-compose.integration.yml` ni en los recursos asignados a los contenedores (tampoco se incorporó Redis/Dragonfly, descartado en el Día 5). Por lo tanto, **esta baseline representa directamente el rendimiento de la configuración final**, evitando generar ejecuciones y escrituras redundantes en MongoDB.
