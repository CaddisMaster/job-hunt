import pytest


@pytest.mark.criterion(1, "The app answers health checks")
def test_healthz_reports_ok_version_and_database(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"  # "ok" only after SELECT 1 succeeded
    assert body["version"]  # "dev" locally, the release version in an image


def test_healthz_reports_a_database_it_cannot_reach(client, monkeypatch):
    from sqlalchemy.orm import Session

    def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(Session, "execute", broken)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["status"] == "db_unavailable"


def test_head_requests_are_answered_like_get(client):
    response = client.head("/healthz")
    assert response.status_code == 200
    assert response.content == b""
