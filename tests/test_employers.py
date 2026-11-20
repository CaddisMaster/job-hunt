"""The shared employer registry (#43). No test reaches a hiring system: the one live
request (employers._get) is replaced, and every test's board token is unique."""

import uuid

import httpx2
import pytest
from sqlalchemy import delete, select

from app import employers
from app.db import SessionLocal
from app.employers import Board, RegistryError, parse_careers_url
from app.models import Employer
from tests.conftest import csrf, login


@pytest.fixture
def token():
    value = f"test-{uuid.uuid4().hex[:12]}"
    yield value
    with SessionLocal() as db:
        db.execute(delete(Employer).where(Employer.token == value))
        db.commit()


@pytest.fixture
def board_api(monkeypatch):
    """Stands in for the hiring systems: answers every check with `status` and `body`,
    and records each URL asked."""

    class Api:
        status = 200
        body: object = {"name": "Duolingo"}
        error: Exception | None = None
        asked: list[str] = []

    api = Api()
    api.asked = []

    def fake_get(url):
        api.asked.append(url)
        if api.error:
            raise api.error
        return httpx2.Response(api.status, json=api.body, request=httpx2.Request("GET", url))

    monkeypatch.setattr(employers, "_get", fake_get)
    return api


@pytest.fixture
def admin_client(client, make_user):
    admin = make_user(is_admin=True)
    assert login(client, admin.email).status_code == 303
    return client


def add(client, careers_url, name=""):
    form = {"careers_url": careers_url, "name": name, "csrf_token": csrf(client)}
    return client.post("/admin/employers", data=form, follow_redirects=False)


def saved(token) -> Employer | None:
    with SessionLocal() as db:
        return db.scalar(select(Employer).where(Employer.token == token))


@pytest.mark.parametrize(
    ("url", "system", "token"),
    [
        ("https://boards.greenhouse.io/duolingo", "greenhouse", "duolingo"),
        ("https://job-boards.greenhouse.io/duolingo/jobs/7012345", "greenhouse", "duolingo"),
        ("https://boards.greenhouse.io/embed/job_board?for=duolingo", "greenhouse", "duolingo"),
        ("boards.greenhouse.io/duolingo", "greenhouse", "duolingo"),
        ("https://jobs.lever.co/aurora-innovation", "lever", "aurora-innovation"),
        ("https://jobs.lever.co/aurora-innovation/0e7c-…/apply", "lever", "aurora-innovation"),
        ("https://jobs.ashbyhq.com/acme.inc?utm_source=x", "ashby", "acme.inc"),
        ("  HTTPS://JOBS.ASHBYHQ.COM/Gecko  ", "ashby", "Gecko"),
    ],
)
def test_the_system_and_token_are_read_from_the_url(url, system, token):
    assert parse_careers_url(url) == Board(system, token)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/company/duolingo/jobs",
        "https://careers.duolingo.com",
        "https://boards.greenhouse.io.evil.example/duolingo",
        "https://evil.example/boards.greenhouse.io/duolingo",
        "ftp://jobs.lever.co/aurora",
        "https://boards.greenhouse.io/",
        "https://jobs.lever.co/../../admin",
        "https://boards.greenhouse.io/embed/job_board",
    ],
)
def test_anything_else_is_refused(url):
    with pytest.raises(RegistryError):
        parse_careers_url(url)


@pytest.mark.criterion(43, "The admin adds an employer from its careers URL")
@pytest.mark.parametrize(
    ("url_format", "system", "api_url"),
    [
        ("https://boards.greenhouse.io/{}", "greenhouse", "https://boards-api.greenhouse.io/v1/boards/{}"),
        ("https://jobs.lever.co/{}", "lever", "https://api.lever.co/v0/postings/{}?mode=json&limit=1"),
        ("https://jobs.ashbyhq.com/{}", "ashby", "https://api.ashbyhq.com/posting-api/job-board/{}"),
    ],
)
def test_the_admin_adds_an_employer_after_one_check(admin_client, board_api, token, url_format, system, api_url):
    response = add(admin_client, url_format.format(token), name="Duolingo")
    assert response.status_code == 303

    assert board_api.asked == [api_url.format(token)]
    employer = saved(token)
    assert (employer.name, employer.system, employer.enabled) == ("Duolingo", system, True)
    assert employer.careers_url == url_format.format(token)
    page = admin_client.get("/admin/employers").text
    assert "Duolingo" in page and token in page


def test_greenhouse_names_the_employer_when_the_name_is_blank(admin_client, board_api, token):
    board_api.body = {"name": "Duolingo", "content": ""}
    assert add(admin_client, f"https://boards.greenhouse.io/{token}").status_code == 303
    assert saved(token).name == "Duolingo"


def test_lever_and_ashby_need_the_name_typed(admin_client, board_api, token):
    board_api.body = []
    response = add(admin_client, f"https://jobs.lever.co/{token}")
    assert response.status_code == 422
    assert "Type it in" in response.text
    assert saved(token) is None


@pytest.mark.criterion(43, "A URL that isn't a known hiring system is refused")
def test_an_unknown_url_saves_nothing_and_names_the_supported_systems(admin_client, board_api, token):
    response = add(admin_client, f"https://careers.example.com/{token}", name="Example")
    assert response.status_code == 422
    assert "Greenhouse" in response.text and "Lever" in response.text and "Ashby" in response.text
    assert board_api.asked == []  # nothing is requested for a URL that isn't one of the three
    assert saved(token) is None


def test_a_board_the_system_doesnt_have_is_refused(admin_client, board_api, token):
    board_api.status, board_api.body = 404, {"status": 404, "error": "Job board not found"}
    response = add(admin_client, f"https://boards.greenhouse.io/{token}", name="Nobody")
    assert response.status_code == 422
    assert "has no job board called" in response.text
    assert saved(token) is None


@pytest.mark.parametrize("error", [httpx2.ConnectTimeout("timed out"), httpx2.ConnectError("refused")])
def test_a_system_that_cant_be_reached_saves_nothing(admin_client, board_api, token, error):
    board_api.error = error
    response = add(admin_client, f"https://jobs.ashbyhq.com/{token}", name="Gecko")
    assert response.status_code == 422
    assert "reach Ashby to check" in response.text
    assert saved(token) is None


def test_a_server_error_saves_nothing(admin_client, board_api, token):
    board_api.status = 503
    response = add(admin_client, f"https://jobs.lever.co/{token}", name="Aurora")
    assert response.status_code == 422 and "503" in response.text
    assert saved(token) is None


def test_the_same_board_isnt_added_twice(admin_client, board_api, token):
    assert add(admin_client, f"https://boards.greenhouse.io/{token}", name="Duolingo").status_code == 303
    response = add(admin_client, f"https://job-boards.greenhouse.io/{token}/jobs/1", name="Duolingo again")
    assert response.status_code == 422
    assert "already in the registry" in response.text
    assert len(board_api.asked) == 1


@pytest.mark.criterion(43, "A disabled employer isn't fetched")
def test_a_disabled_employer_isnt_in_the_crawls_list(admin_client, board_api, token):
    assert add(admin_client, f"https://boards.greenhouse.io/{token}", name="Duolingo").status_code == 303
    employer_id = saved(token).id
    with SessionLocal() as db:
        assert employer_id in {e.id for e in employers.to_fetch(db)}

    response = admin_client.post(f"/admin/employers/{employer_id}/disable", data={"csrf_token": csrf(admin_client)}, follow_redirects=False)
    assert response.status_code == 303
    assert saved(token).enabled is False
    with SessionLocal() as db:
        assert employer_id not in {e.id for e in employers.to_fetch(db)}

    admin_client.post(f"/admin/employers/{employer_id}/enable", data={"csrf_token": csrf(admin_client)}, follow_redirects=False)
    with SessionLocal() as db:
        assert employer_id in {e.id for e in employers.to_fetch(db)}


@pytest.mark.criterion(43, "Only the admin manages the registry")
def test_a_user_who_isnt_the_admin_gets_404(client, make_user, board_api, token):
    with SessionLocal() as db:
        employer = Employer(name="Duolingo", system="greenhouse", token=token, careers_url="https://boards.greenhouse.io/x")
        db.add(employer)
        db.commit()
        employer_id = employer.id
    user = make_user()
    login(client, user.email)

    assert client.get("/admin/employers").status_code == 404
    form = {"careers_url": f"https://boards.greenhouse.io/{token}-2", "name": "X", "csrf_token": csrf(client)}
    assert client.post("/admin/employers", data=form).status_code == 404
    assert client.post(f"/admin/employers/{employer_id}/disable", data={"csrf_token": csrf(client)}).status_code == 404
    assert saved(token).enabled is True
    assert board_api.asked == []



# ---- The starter list -----------------------------------------------------------------


@pytest.fixture
def starter_csv(tmp_path, monkeypatch):
    """A starter list of three made-up boards, one with a bad token, cleaned up after."""
    tokens = [f"test-{uuid.uuid4().hex[:12]}" for _ in range(2)]
    path = tmp_path / "employers.csv"
    path.write_text(
        "name,system,token\n"
        f"Alpha Co,greenhouse,{tokens[0]}\n"
        f"Beta Co,ashby,{tokens[1]}\n"
        "Evil Co,greenhouse,../../etc\n"
        f"Gamma Co,workday,{tokens[0]}\n"
    )
    monkeypatch.setattr(employers, "STARTERS", path)
    yield tokens
    with SessionLocal() as db:
        db.execute(delete(Employer).where(Employer.token.in_(tokens)))
        db.commit()


def test_the_starter_list_adds_only_valid_rows_once(admin_client, starter_csv, board_api):
    response = admin_client.post("/admin/employers/starters", data={"csrf_token": csrf(admin_client)}, follow_redirects=True)
    assert "Added 2 employers from the Pittsburgh starter list (0 already here)." in response.text
    alpha, beta = saved(starter_csv[0]), saved(starter_csv[1])
    assert (alpha.name, alpha.system, alpha.careers_url) == ("Alpha Co", "greenhouse", f"https://boards.greenhouse.io/{starter_csv[0]}")
    assert (beta.system, beta.careers_url) == ("ashby", f"https://jobs.ashbyhq.com/{starter_csv[1]}")
    assert board_api.asked == []  # no live check per row

    with SessionLocal() as db:
        db.scalar(select(Employer).where(Employer.token == starter_csv[0])).enabled = False
        db.commit()
    response = admin_client.post("/admin/employers/starters", data={"csrf_token": csrf(admin_client)}, follow_redirects=True)
    assert "Added 0 employers from the Pittsburgh starter list (2 already here)." in response.text
    assert saved(starter_csv[0]).enabled is False  # a disabled one stays disabled


def test_the_starter_list_needs_an_admin(client, logged_in, starter_csv):
    response = client.post("/admin/employers/starters", data={"csrf_token": csrf(client)}, follow_redirects=False)
    assert response.status_code == 404
    assert saved(starter_csv[0]) is None


def test_the_shipped_starter_list_is_valid():
    rows = employers.starters()
    assert len(rows) >= 20
    with employers.STARTERS.open() as f:
        assert sum(1 for _ in f) - 1 == len(rows)  # every row passed the checks
    assert len({board for _, board in rows}) == len(rows)  # no board twice
