import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from admin import router as admin_router
from db import load_catalog, pool
from errors import error_response, install_error_handlers
from search import load_search_catalog, router as search_router
from seats import router as seats_router

AUTHORIZATION = f"Bearer {os.environ['AIRLINE_API_KEY']}".encode()
BOOT_ID = str(uuid.uuid4())
logger = logging.getLogger(__name__)


class RequestModel(BaseModel):
    model_config = ConfigDict(strict=True)


@asynccontextmanager
async def lifespan(app):
    load_catalog()
    load_search_catalog()
    pool.open()
    yield
    pool.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
install_error_handlers(app)
app.include_router(search_router)
app.include_router(seats_router)
app.include_router(admin_router)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    started = time.perf_counter()
    authorization = request.headers.get("authorization", "").encode("latin-1")
    if (request.method == "GET" and request.url.path == "/ui") or secrets.compare_digest(authorization, AUTHORIZATION):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request error")
            response = error_response(500, "internal_server_error", "Internal server error")
    else:
        response = error_response(401, "unauthorized", "A valid bearer API key is required")
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
    return response


@app.get("/health")
def health():
    try:
        with psycopg.connect() as connection:
            connection.execute("SELECT 1")
    except psycopg.OperationalError:
        return error_response(503, "database_unavailable", "Database is unavailable")
    return {"status": "ok", "boot_id": BOOT_ID, "version": "0.1.0"}


@app.get("/ui")
def ui():
    return FileResponse("static/index.html")
