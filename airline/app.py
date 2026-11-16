import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from http import HTTPStatus

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

from db import load_catalog

AUTHORIZATION = f"Bearer {os.environ['AIRLINE_API_KEY']}".encode()
BOOT_ID = str(uuid.uuid4())
logger = logging.getLogger(__name__)


class RequestModel(BaseModel):
    model_config = ConfigDict(strict=True)


@asynccontextmanager
async def lifespan(app):
    load_catalog()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    detail = "; ".join(
        f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
        for error in exc.errors()
    )
    return JSONResponse({"error": "validation_error", "detail": detail}, status_code=422)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse(
        {"error": HTTPStatus(exc.status_code).name.lower(), "detail": str(exc.detail)},
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.middleware("http")
async def authenticate(request: Request, call_next):
    started = time.perf_counter()
    authorization = request.headers.get("authorization", "").encode("latin-1")
    if secrets.compare_digest(authorization, AUTHORIZATION):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request error")
            response = JSONResponse(
                {"error": "internal_server_error", "detail": "Internal server error"},
                status_code=500,
            )
    else:
        response = JSONResponse(
            {"error": "unauthorized", "detail": "A valid bearer API key is required"},
            status_code=401,
        )
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
    return response


@app.get("/health")
def health():
    try:
        with psycopg.connect() as connection:
            connection.execute("SELECT 1")
    except psycopg.OperationalError:
        return JSONResponse(
            {"error": "database_unavailable", "detail": "Database is unavailable"},
            status_code=503,
        )
    return {"status": "ok", "boot_id": BOOT_ID, "version": "0.1.0"}
