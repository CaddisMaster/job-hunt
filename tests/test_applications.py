"""#76: applications, site notes, /apply next, and the MCP server that gives Claude
Code the bridge as tools.

Real tokens and the real app. The MCP server's one network seam (`_request`) is
pointed at the test client, so its tools are exercised end to end.
"""

import base64
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Application, SavedJob, SiteNote, TailoredResume
from tests.conftest import login
from tests.test_api import bearer, make_token

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ROOT = Path(__file__).resolve().parent.parent


def load_mcp():
    spec = importlib.util.spec_from_file_location("jobhunt_mcp", ROOT / "tools" / "jobhunt_mcp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def me(client, make_user):
    """A logged-in user with a token and three saved jobs: two with approved tailored
    resumes (the older first), one with a draft."""
    user = make_user()
    assert login(client, user.email).status_code == 303
    now = datetime.now(UTC)
    with SessionLocal() as db:
        jobs = []
        for n, (host, approved) in enumerate([("jobs.lever.co", True), ("careers.example.com", True), ("jobs.ashbyhq.com", False)]):
            job = SavedJob(user_id=user.id, url=f"https://{host}/acme/{n}", source="page", title=f"Job {n}", created_at=now - timedelta(days=3 - n))  # fmt: skip
            db.add(job)
            db.flush()
            db.add(TailoredResume(user_id=user.id, saved_job_id=job.id, content={}, pdf=b"%PDF-1.4", model="claude-code", approved_at=now if approved else None))  # fmt: skip
            jobs.append(job.id)
        db.commit()
    user.jobs = jobs
    user.token = make_token(client)
    return user


def record(client, user, job_id, **body):
    payload = {"outcome": "submitted", "answers_used": [], **body}
    return client.post(f"/api/jobs/{job_id}/application", json=payload, headers=bearer(user.token))


# ---- Next ------------------------------------------------------------------------------


@pytest.mark.criterion(76, "The next job to apply to")
def test_the_next_job_is_the_oldest_approved_one_not_yet_applied_to(client, me):
    first, second, draft = me.jobs
    nxt = client.get("/api/apply/next", headers=bearer(me.token))
    assert nxt.status_code == 200 and nxt.json()["id"] == first and nxt.json()["ready_to_apply"]
    record(client, me, first)
    assert client.get("/api/apply/next", headers=bearer(me.token)).json()["id"] == second
    record(client, me, second, outcome="stopped", note="It asked for a cover letter.")
    assert client.get("/api/apply/next", headers=bearer(me.token)).status_code == 204  # the draft isn't offered


# ---- Recording --------------------------------------------------------------------------


@pytest.mark.criterion(76, "A submitted application is recorded")
def test_a_submitted_application_is_recorded_and_shown(client, me):
    job = me.jobs[0]
    used = [{"question": "Are you legally authorized to work in the United States?", "answer": "Yes"}]
    response = record(client, me, job, answers_used=used, screenshot_png_base64=base64.b64encode(PNG).decode())
    assert response.status_code == 201
    assert response.json()["outcome"] == "submitted" and response.json()["has_screenshot"] is True
    page = client.get(f"/saved/{job}").text
    assert "Applied " in page and "Are you legally authorized to work in the United States?" in page
    shot = client.get(f"/saved/{job}/application/screenshot.png")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png" and shot.content == PNG
    package = client.get(f"/api/jobs/{job}/package", headers=bearer(me.token)).json()
    assert package["application"]["answers_used"] == used


@pytest.mark.criterion(76, "A stopped application says why")
def test_a_stopped_application_says_why(client, me):
    job = me.jobs[0]
    assert record(client, me, job, outcome="stopped").status_code == 422  # no reason
    assert record(client, me, job, outcome="stopped", note="A CAPTCHA: yours to answer.").status_code == 201
    page = client.get(f"/saved/{job}").text
    assert "Stopped " in page and "A CAPTCHA: yours to answer." in page
    assert client.get("/api/apply/next", headers=bearer(me.token)).json()["id"] != job


def test_recording_again_replaces_the_record(client, me):
    job = me.jobs[0]
    record(client, me, job, outcome="stopped", note="Needs an account.")
    record(client, me, job, outcome="submitted")
    with SessionLocal() as db:
        rows = db.scalars(select(Application).where(Application.saved_job_id == job)).all()
    assert [(r.outcome, r.note) for r in rows] == [("submitted", "")]


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"outcome": "maybe"}, "outcome is submitted or stopped."),
        ({"screenshot_png_base64": "not base64!"}, "screenshot isn't base64."),
        ({"screenshot_png_base64": base64.b64encode(b"GIF89a....").decode()}, "screenshot must be a PNG."),
    ],
)
def test_a_bad_record_is_refused(client, me, body, error):
    response = record(client, me, me.jobs[0], **body)
    assert response.status_code == 422 and response.json()["error"] == error


# ---- Site notes ---------------------------------------------------------------------------


@pytest.mark.criterion(76, "What Claude learned about a site carries over")
def test_a_site_note_carries_over_to_the_next_job_on_that_site(client, me):
    note = "Lever: the resume upload is the first field; 'How did you hear about us' is required."
    put = client.put("/api/site-notes/Jobs.Lever.co", json={"note": note}, headers=bearer(me.token))
    assert put.status_code == 200 and put.json()["host"] == "jobs.lever.co"
    package = client.get(f"/api/jobs/{me.jobs[0]}/package", headers=bearer(me.token)).json()
    assert package["site"] == {"host": "jobs.lever.co", "note": note}
    assert client.get(f"/api/jobs/{me.jobs[1]}/package", headers=bearer(me.token)).json()["site"]["note"] is None
    assert "What Claude knows about jobs.lever.co" in client.get(f"/saved/{me.jobs[0]}").text
    # Saving again replaces it.
    client.put("/api/site-notes/jobs.lever.co", json={"note": "Shorter."}, headers=bearer(me.token))
    assert client.get("/api/site-notes/jobs.lever.co", headers=bearer(me.token)).json()["note"] == "Shorter."


@pytest.mark.parametrize("host", ["not a host", "-bad.example.com", "localhost", "x" * 300 + ".com"])
def test_a_site_note_needs_a_real_host(client, me, host):
    response = client.put(f"/api/site-notes/{host}", json={"note": "x"}, headers=bearer(me.token))
    assert response.status_code in (404, 422)


# ---- Private --------------------------------------------------------------------------------


@pytest.mark.criterion(76, "Applications and site notes are mine alone")
def test_applications_and_site_notes_are_mine_alone(client, me, make_user):
    record(client, me, me.jobs[0], screenshot_png_base64=base64.b64encode(PNG).decode())
    client.put("/api/site-notes/jobs.lever.co", json={"note": "Mine."}, headers=bearer(me.token))
    other = make_user()
    client.cookies.clear()
    assert login(client, other.email).status_code == 303
    theirs = make_token(client)
    assert client.get(f"/saved/{me.jobs[0]}/application/screenshot.png").status_code == 404
    assert record(client, type("U", (), {"token": theirs}), me.jobs[0]).status_code == 404
    assert client.get("/api/site-notes/jobs.lever.co", headers=bearer(theirs)).json()["note"] is None
    assert client.get("/api/apply/next", headers=bearer(theirs)).status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(SiteNote.note).where(SiteNote.user_id == me.id)) == "Mine."


# ---- The MCP server ---------------------------------------------------------------------------


@pytest.fixture
def mcp(client, me, monkeypatch, tmp_path):
    """The MCP server, its token from the environment, its requests into the app."""
    module = load_mcp()
    monkeypatch.setenv("JOBHUNT_TOKEN", me.token)
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    seen = []

    def request(method, path, body=None):
        seen.append((method, path))
        response = client.request(method, path, json=body, headers={"Authorization": f"Bearer {module._token()}"})
        return response.status_code, {k.lower(): v for k, v in response.headers.items()}, response.content

    monkeypatch.setattr(module, "_request", request)
    module.seen = seen
    return module


def call(module, name, **arguments):
    reply = module.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    result = reply["result"]
    return result["isError"], result["content"][0]["text"]


@pytest.mark.criterion(76, "The MCP server offers the bridge as tools")
def test_the_mcp_server_offers_the_bridge_as_tools(mcp, me):
    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["protocolVersion"] == "2025-06-18" and "tools" in init["result"]["capabilities"]
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = {t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert tools == {
        "list_jobs", "next_job", "get_job_package", "get_resume_and_profile", "send_profile", "send_tailored_resume", "approve_tailored_resume",
        "download_resume_pdf", "add_answer", "record_application", "get_site_note", "save_site_note",
    }  # fmt: skip
    is_error, text = call(mcp, "next_job")
    assert not is_error and json.loads(text)["id"] == me.jobs[0]
    assert mcp.seen[-1] == ("GET", "/api/apply/next")
    assert me.token not in text


def test_the_mcp_server_downloads_the_pdf_and_records_with_a_screenshot(mcp, me, tmp_path):
    is_error, text = call(mcp, "download_resume_pdf", job_id=me.jobs[0])
    saved = json.loads(text)
    assert not is_error and Path(saved["path"]).read_bytes().startswith(b"%PDF") and saved["approved"] is True
    assert Path(saved["path"]).is_relative_to(tmp_path)
    shot = tmp_path / "confirmation.png"
    shot.write_bytes(PNG)
    is_error, text = call(mcp, "record_application", job_id=me.jobs[0], outcome="submitted", screenshot_path=str(shot))
    assert not is_error and json.loads(text)["has_screenshot"] is True


def test_the_mcp_servers_errors_are_tool_errors_without_the_token(mcp, me):
    is_error, text = call(mcp, "get_job_package", job_id=999_999_999)
    assert is_error and text == "Job Hunt answered 404: Not found." and me.token not in text
    is_error, text = call(mcp, "get_job_package", job_id="one")
    assert is_error and "job_id must be" in text
    is_error, text = call(mcp, "no_such_tool")
    assert is_error
    unknown = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert unknown["error"]["code"] == -32601


def test_without_a_token_the_mcp_server_says_how_to_add_one(monkeypatch, tmp_path):
    module = load_mcp()
    monkeypatch.delenv("JOBHUNT_TOKEN", raising=False)
    monkeypatch.setattr(module, "TOKEN_FILE", tmp_path / "token")
    monkeypatch.setattr(module.sys, "platform", "linux")
    with pytest.raises(module.ToolError, match="save it to .*token"):
        module._token()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 44, "stdout": ""})())
    with pytest.raises(module.ToolError, match="security add-generic-password -s jobhunt-token"):
        module._token()


def test_the_token_file_is_read_only_when_its_yours_alone(monkeypatch, tmp_path):
    module = load_mcp()
    monkeypatch.delenv("JOBHUNT_TOKEN", raising=False)
    token = tmp_path / "token"
    token.write_text("jh_from-the-file\n")
    monkeypatch.setattr(module, "TOKEN_FILE", token)
    token.chmod(0o644)
    with pytest.raises(module.ToolError, match="chmod 600"):
        module._token()
    token.chmod(0o600)
    assert module._token() == "jh_from-the-file"


def test_the_mcp_server_speaks_line_delimited_json_on_stdio(monkeypatch, capsys):
    import io

    module = load_mcp()
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "", "not json", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})]  # fmt: skip
    monkeypatch.setattr(module.sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    assert module.main() == 0
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert out == [{"jsonrpc": "2.0", "id": 1, "result": {}}, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}]  # fmt: skip
