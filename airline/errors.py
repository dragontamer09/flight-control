from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status_code, error, detail=""):
        self.status_code = status_code
        self.error = error
        self.detail = detail


def error_response(status_code, error, detail=""):
    return JSONResponse({"error": error, "detail": detail}, status_code=status_code)


def install_error_handlers(app: FastAPI):
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return error_response(exc.status_code, exc.error, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return error_response(422, "validation_error", detail)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        response = error_response(
            exc.status_code, HTTPStatus(exc.status_code).name.lower(), str(exc.detail)
        )
        response.headers.update(exc.headers or {})
        return response
