import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.criterion(1, "An unhandled exception shows the error page")
def test_an_unhandled_exception_renders_error_html_without_a_traceback():
    app = create_app()
    router = APIRouter()

    @router.get("/boom")
    def boom():
        raise RuntimeError("secret internals: password=hunter2")

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert "Something went wrong" in response.text
    assert "secret internals" not in response.text
    assert "Traceback" not in response.text


def test_an_unknown_page_is_a_friendly_404(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "Not found." in response.text
