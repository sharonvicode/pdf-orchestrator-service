"""Errores HTTP en formato Problem Details (RFC 9457)."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions import OrchestratorError

PROBLEM_JSON = "application/problem+json"

logger = logging.getLogger(__name__)


def problem_response(
    request: Request,
    status: int,
    detail: str,
    title: str | None = None,
    type_: str = "about:blank",
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    body = {
        "type": type_,
        "title": title or HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        **extensions,
    }
    return JSONResponse(
        status_code=status,
        content=jsonable_encoder(body),
        media_type=PROBLEM_JSON,
        headers=headers,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return problem_response(request, exc.status_code, str(exc.detail), headers=exc.headers)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return problem_response(
        request,
        422,
        "El request no es válido.",
        title="Unprocessable Content",
        errors=errors,
    )


async def orchestrator_exception_handler(request: Request, exc: OrchestratorError) -> JSONResponse:
    return problem_response(request, exc.status_code, exc.detail, title=exc.title)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # El detalle queda solo en el log; al cliente nunca se le expone.
    logger.exception("Error inesperado procesando %s %s", request.method, request.url.path)
    return problem_response(request, 500, "Ocurrió un error inesperado.")


def register_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(OrchestratorError, orchestrator_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
