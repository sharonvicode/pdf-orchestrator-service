// Baseline de rendimiento para POST /extraer del PDF ExtractText Orchestrator.
//
// Flujo completo:
//   k6 -> Traefik (:8080) -> Orchestrator -> Traefik (:8081, Consul) -> Validator -> Extractor -> Persistence -> MongoDB
//
// Uso (desde la raíz del repositorio, con docker-compose.integration.yml levantado):
//   docker run --rm --network pdf-integracion-edge \
//     -v "${PWD}/tests/stress:/scripts:ro" \
//     -v "${PWD}/../pdf-extract-service/tests/stress/pdfs:/pdfs:ro" \
//     -e BASE_URL=http://traefik:80 -e VUS=2 -e DURATION=15s \
//     grafana/k6:latest run /scripts/k6-baseline.js
import http from "k6/http";
import { check } from "k6";

const BASE_URL = (__ENV.BASE_URL || "http://localhost:8080").replace(/\/$/, "");
const PDF_PATH = __ENV.PDF_PATH || "/pdfs/2020-Scrum-Guide-Spanish-Latin-South-American.pdf";
const PDF_NAME = PDF_PATH.split("/").pop();
const VUS = Number(__ENV.VUS || 2);
const DURATION = __ENV.DURATION || "15s";

const pdfBytes = open(PDF_PATH, "b");

export const options = {
  vus: VUS,
  duration: DURATION,
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  const body = {
    file: http.file(pdfBytes, PDF_NAME, "application/pdf"),
  };

  const response = http.post(`${BASE_URL}/extraer`, body);

  check(response, {
    "status 200": (r) => r.status === 200,
    "exito true": (r) => {
      try {
        return r.json("exito") === true;
      } catch (_) {
        return false;
      }
    },
  });
}
