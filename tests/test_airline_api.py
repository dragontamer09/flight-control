import importlib.util
from pathlib import Path
import sys
from unittest.mock import MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient


def test_common_api_contract(monkeypatch):
    airline_dir = Path(__file__).resolve().parents[1] / "airline"
    monkeypatch.syspath_prepend(str(airline_dir))
    monkeypatch.setenv("AIRLINE_API_KEY", "test-key")
    spec = importlib.util.spec_from_file_location("airline_app_test", airline_dir / "app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    class Payload(module.RequestModel):
        passengers: int

    @module.app.post("/_contract")
    def validate(payload: Payload):
        return payload.model_dump()

    @module.app.get("/_contract/error")
    def known_error():
        raise HTTPException(409, "No seats", headers={"X-Error": "seats"})

    @module.app.get("/_contract/crash")
    def crash():
        raise RuntimeError("private exception details")

    # No lifespan: these shared API checks do not need a running database.
    client = TestClient(module.app, raise_server_exceptions=False)
    try:
        def check(response, status):
            assert response.status_code == status
            timing = response.headers["Server-Timing"]
            assert timing.startswith("app;dur=")
            assert float(timing.split("=", 1)[1]) >= 0
            if status >= 400:
                body = response.json()
                assert set(body) == {"error", "detail"}
                assert isinstance(body["error"], str) and body["error"]
                assert isinstance(body["detail"], str) and body["detail"]
            return response

        for authorization in (None, "Bearer wrong-key", "Basic test-key", "Bearer "):
            headers = {} if authorization is None else {"Authorization": authorization}
            check(client.get("/health", headers=headers), 401)

        client.headers["Authorization"] = "Bearer test-key"
        connection = MagicMock()
        monkeypatch.setattr(module.psycopg, "connect", connection)
        response = check(client.get("/health"), 200)
        assert response.json()["status"] == "ok"
        connection.side_effect = module.psycopg.OperationalError("private database details")
        response = check(client.get("/health"), 503)
        assert response.json() == {"error": "database_unavailable", "detail": "Database is unavailable"}
        check(client.post("/_contract", json={"passengers": 2}), 200)
        for payload in ({}, {"passengers": "2"}, {"passengers": True},
                        {"passengers": 2.5}, {"passengers": None}):
            response = check(client.post("/_contract", json=payload), 422)
            assert response.json()["error"] == "validation_error"
        check(client.post("/_contract", content="{", headers={"Content-Type": "application/json"}), 422)
        check(client.get("/unknown"), 404)
        check(client.delete("/_contract"), 405)
        response = check(client.get("/_contract/error"), 409)
        assert response.json() == {"error": "conflict", "detail": "No seats"}
        assert response.headers["X-Error"] == "seats"
        response = check(client.get("/_contract/crash"), 500)
        assert "private exception details" not in response.text
        # The service adds no throttling, including on repeated requests.
        for _ in range(30):
            check(client.post("/_contract", json={"passengers": 1}), 200)
    finally:
        client.close()

