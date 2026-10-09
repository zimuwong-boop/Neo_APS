import pytest
from django.db import connection


@pytest.mark.django_db
def test_health_checks_postgresql(client):
    assert connection.vendor == "postgresql"
    response = client.get("/api/v1/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_api_does_not_return_spa(client):
    assert client.get("/api/v1/not-a-route/").status_code == 404


def test_missing_asset_returns_404(client):
    assert client.get("/assets/not-a-file.js").status_code == 404
