"""#88: web-driven runs. A button queues a run, the runner claims it, and the run's
prompts are answered on the job's page.

Real app, real tokens: the runner and a run's tools are played by API calls with the
user's token, Sean by his logged-in session.
"""

import base64
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Run, RunEvent, SavedJob
from tests.conftest import csrf, login
from tests.test_api import bearer, make_token

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def me(client, make_user):
    user = make_user()
    assert login(client, user.email).status_code == 303
    with SessionLocal() as db:
        jobs = [SavedJob(user_id=user.id, url=f"https://jobs.lever.co/acme/{n}", source="page", title=f"Job {n}") for n in range(2)]
        db.add_all(jobs)
        db.commit()
        user.jobs = [j.id for j in jobs]
    user.token = make_token(client)
    return user


def apply(client, job_id):
    return client.post(f"/saved/{job_id}/apply", data={"csrf_token": csrf(client)}, follow_redirects=False)


def claim(client, user):
    return client.post("/api/runs/claim", headers=bearer(user.token))


def runs_of(user) -> list[Run]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(Run).where(Run.user_id == user.id).order_by(Run.id)))
        db.expunge_all()
        return rows


def prompt(client, user, run_id, **body):
    return client.post(f"/api/runs/{run_id}/prompts", json=body, headers=bearer(user.token))


def prompt_state(client, user, run_id, prompt_id):
    return client.get(f"/api/runs/{run_id}/prompts/{prompt_id}", headers=bearer(user.token)).json()


def answer(client, run_id, prompt_id, value, *, htmx=True):
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(f"/runs/{run_id}/prompts/{prompt_id}", data={"answer": value, "csrf_token": csrf(client)}, headers=headers)


def running(client, user) -> int:
    apply(client, user.jobs[0])
    return claim(client, user).json()["id"]


# ---- Queueing --------------------------------------------------------------------------


@pytest.mark.criterion(88, "A button queues a run")
def test_the_apply_button_queues_a_run_shown_on_the_jobs_page(client, me):
    response = apply(client, me.jobs[0])
    assert response.status_code == 303 and response.headers["location"] == f"/saved/{me.jobs[0]}#run"
    [run] = runs_of(me)
    assert (run.kind, run.saved_job_id, run.status) == ("apply", me.jobs[0], "queued")
    page = client.get(f"/saved/{me.jobs[0]}").text
    assert "waiting for the runner on the VM" in page and f'hx-get="/runs/{run.id}/panel"' in page
    apply(client, me.jobs[0])  # pressing it again is the same run
    assert len(runs_of(me)) == 1


def test_the_build_profile_button_queues_a_profile_run(client, me):
    from tests.test_resumes import make_docx, upload

    upload(client, make_docx("Jordan Example\nStaff Accountant"), "resume.docx")
    assert "Build my profile" in client.get("/profile").text
    client.post("/profile/build", data={"csrf_token": csrf(client)})
    [run] = runs_of(me)
    assert (run.kind, run.saved_job_id) == ("profile", None)
    assert "Building your profile" in client.get("/profile").text


@pytest.mark.criterion(88, "Only Apply and Profile can be queued")
def test_only_apply_and_profile_can_be_queued(client, me):
    from app import runs

    with SessionLocal() as db:
        user = db.get(type(me), me.id)
        for kind in ("shell", "apply; rm -rf /", ""):
            with pytest.raises(runs.Invalid):
                runs.queue(db, user, kind)
        with pytest.raises(runs.Invalid):
            runs.queue(db, user, "apply")  # no job
    assert runs_of(me) == []
    # There's no route that takes a command at all: the button posts nothing but the CSRF token.
    assert client.post(f"/saved/{me.jobs[0]}/apply", data={"csrf_token": csrf(client), "command": "anything"}).status_code in (200, 303)
    assert [r.kind for r in runs_of(me)] == ["apply"]


def test_a_job_waiting_for_its_text_cant_be_applied_to(client, me):
    with SessionLocal() as db:
        db.execute(update(SavedJob).where(SavedJob.id == me.jobs[0]).values(source="pending"))
        db.commit()
    apply(client, me.jobs[0])
    assert runs_of(me) == []


# ---- Claiming ----------------------------------------------------------------------------


@pytest.mark.criterion(88, "The runner takes one run at a time")
def test_the_runner_takes_one_run_at_a_time(client, me):
    apply(client, me.jobs[0])
    apply(client, me.jobs[1])
    first = claim(client, me)
    assert first.status_code == 200 and first.json()["job_id"] == me.jobs[0] and first.json()["status"] == "running"
    assert claim(client, me).status_code == 204  # one is running
    client.post(f"/api/runs/{first.json()['id']}/finish", json={"status": "done", "summary": "Applied."}, headers=bearer(me.token))  # fmt: skip
    assert claim(client, me).json()["job_id"] == me.jobs[1]


def test_a_run_left_running_too_long_is_failed_so_the_queue_moves(client, me):
    run_id = running(client, me)
    apply(client, me.jobs[1])
    with SessionLocal() as db:
        db.execute(update(Run).where(Run.id == run_id).values(started_at=datetime.now(UTC) - timedelta(hours=3)))
        db.commit()
    assert claim(client, me).json()["job_id"] == me.jobs[1]
    assert runs_of(me)[0].status == "failed"


def test_a_restarted_runner_fails_what_was_running(client, me):
    running(client, me)
    assert client.post("/api/runs/abandon", headers=bearer(me.token)).json() == {"failed": 1}
    assert runs_of(me)[0].status == "failed" and "restarted" in runs_of(me)[0].summary


# ---- Prompts -------------------------------------------------------------------------------


@pytest.mark.criterion(88, "A question is answered on the page")
def test_a_question_is_answered_on_the_page(client, me):
    run_id = running(client, me)
    client.post(f"/api/runs/{run_id}/progress", json={"text": "Tailoring your resume."}, headers=bearer(me.token))
    asked = prompt(client, me, run_id, kind="question", text="Will you relocate to Cleveland?", options=["Yes", "No"])
    prompt_id = asked.json()["prompt_id"]
    assert prompt_state(client, me, run_id, prompt_id) == {"state": "waiting", "answer": None}
    page = client.get(f"/saved/{me.jobs[0]}").text
    assert "Tailoring your resume." in page and "Needs you:" in page and "Will you relocate to Cleveland?" in page
    assert 'value="Yes"' in page and 'value="No"' in page
    response = answer(client, run_id, prompt_id, "No")
    assert response.status_code == 200 and "→ No" in response.text  # the panel, swapped in place
    assert prompt_state(client, me, run_id, prompt_id) == {"state": "answered", "answer": "No"}
    assert answer(client, run_id, prompt_id, "Yes").status_code == 422  # answered already


def test_the_browser_hand_off_waits_for_done(client, me):
    run_id = running(client, me)
    prompt_id = prompt(client, me, run_id, kind="browser", text="Sign in to Workday.").json()["prompt_id"]
    page = client.get(f"/saved/{me.jobs[0]}").text
    assert "Needs you in the browser:" in page and "vnc://localhost:5901" in page
    answer(client, run_id, prompt_id, "anything")
    assert prompt_state(client, me, run_id, prompt_id)["answer"] == "done"


@pytest.mark.criterion(88, "Submit waits for my click in Job Hunt")
def test_submit_waits_for_my_click(client, me):
    run_id = running(client, me)
    assert prompt(client, me, run_id, kind="submit", text="All 3 pages filled.").status_code == 409  # needs the screenshot
    shot = base64.b64encode(PNG).decode()
    prompt_id = prompt(client, me, run_id, kind="submit", text="All 3 pages filled.", screenshot_png_base64=shot).json()["prompt_id"]
    assert prompt_state(client, me, run_id, prompt_id)["state"] == "waiting"
    page = client.get(f"/saved/{me.jobs[0]}").text
    assert "Ready to submit." in page and "Submit application" in page
    image = client.get(f"/runs/{run_id}/prompts/{prompt_id}/screenshot.png")
    assert image.status_code == 200 and image.content == PNG
    assert answer(client, run_id, prompt_id, "yes please").status_code == 422  # only the button's values
    answer(client, run_id, prompt_id, "submit")
    assert prompt_state(client, me, run_id, prompt_id) == {"state": "answered", "answer": "submit"}


def test_dont_submit_stops_the_run(client, me):
    run_id = running(client, me)
    shot = base64.b64encode(PNG).decode()
    prompt_id = prompt(client, me, run_id, kind="submit", text="Filled.", screenshot_png_base64=shot).json()["prompt_id"]
    answer(client, run_id, prompt_id, "stop")
    assert prompt_state(client, me, run_id, prompt_id)["answer"] == "stop"
    assert client.get(f"/api/runs/{run_id}", headers=bearer(me.token)).json()["stop_requested"] is True


# ---- Stopping -------------------------------------------------------------------------------


@pytest.mark.criterion(88, "I can stop a run")
def test_i_can_stop_a_run(client, me):
    run_id = running(client, me)
    prompt_id = prompt(client, me, run_id, kind="question", text="Salary?").json()["prompt_id"]
    response = client.post(f"/runs/{run_id}/stop", data={"csrf_token": csrf(client)}, headers={"HX-Request": "true"})
    assert response.status_code == 200 and "stopping…" in response.text
    assert prompt_state(client, me, run_id, prompt_id)["state"] == "stopped"
    assert prompt(client, me, run_id, kind="question", text="Another?").status_code == 409  # told to stop
    client.post(f"/api/runs/{run_id}/finish", json={"status": "stopped", "summary": "Sean stopped it."}, headers=bearer(me.token))
    page = client.get(f"/saved/{me.jobs[0]}").text
    assert "Sean stopped it." in page and "hx-trigger" not in page  # finished: no more polling


def test_a_queued_run_stops_at_once(client, me):
    apply(client, me.jobs[0])
    [run] = runs_of(me)
    client.post(f"/runs/{run.id}/stop", data={"csrf_token": csrf(client)})
    assert runs_of(me)[0].status == "stopped"
    assert claim(client, me).status_code == 204


@pytest.mark.criterion(88, "A run that waits too long stops")
def test_a_run_that_waits_too_long_stops(client, me):
    run_id = running(client, me)
    prompt_id = prompt(client, me, run_id, kind="question", text="Start date?").json()["prompt_id"]
    with SessionLocal() as db:
        db.execute(update(RunEvent).where(RunEvent.id == prompt_id).values(created_at=datetime.now(UTC) - timedelta(minutes=31)))
        db.commit()
    assert prompt_state(client, me, run_id, prompt_id)["state"] == "timeout"
    assert client.get(f"/api/runs/{run_id}", headers=bearer(me.token)).json()["stop_requested"] is True
    assert "Nobody answered for 30 minutes" in client.get(f"/saved/{me.jobs[0]}").text


# ---- Private ---------------------------------------------------------------------------------


@pytest.mark.criterion(88, "Runs are mine alone")
def test_runs_are_mine_alone(client, me, make_user):
    run_id = running(client, me)
    prompt_id = prompt(client, me, run_id, kind="question", text="Mine?").json()["prompt_id"]
    other = make_user()
    client.cookies.clear()
    assert login(client, other.email).status_code == 303
    theirs = make_token(client)
    assert client.get(f"/runs/{run_id}/panel").status_code == 404
    assert answer(client, run_id, prompt_id, "yes").status_code == 404
    assert client.post(f"/runs/{run_id}/stop", data={"csrf_token": csrf(client)}).status_code == 404
    assert client.get(f"/api/runs/{run_id}", headers=bearer(theirs)).status_code == 404
    assert client.post("/api/runs/claim", headers=bearer(theirs)).status_code == 204
    assert client.post(f"/saved/{me.jobs[0]}/apply", data={"csrf_token": csrf(client)}).status_code == 404
