from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoints_return_ok_status() -> None:
    app = create_app()
    with TestClient(app) as client:
        healthz_response = client.get("/healthz")
        health_response = client.get("/health")

    assert healthz_response.status_code == 200
    assert health_response.status_code == 200

    payload = {"status": "ok"}
    assert healthz_response.json() == payload
    assert health_response.json() == payload
