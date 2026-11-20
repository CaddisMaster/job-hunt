"""The Run panel (#88): what a web-driven run shows on its job's page, and Sean's
answers to it. The runner and the run's tools use /api/runs (app/routers/api.py).

⚠️ Every route is the current user's own runs: someone else's is a 404.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app import runs
from app.db import get_db
from app.models import RunEvent, User
from app.security import require_user
from app.templating import templates

router = APIRouter(prefix="/runs")


def panel(request: Request, db: Session, user: User, run, *, error: str | None = None, status: int = 200) -> HTMLResponse:
    context = {"user": user, "run": run, "events": runs.events(db, run), "waiting": runs.waiting(db, run), "error": error}
    return templates.TemplateResponse(request, "_run_panel.html", context, status_code=status)


def _back(request: Request, run) -> Response:
    """htmx swaps the panel in place; a plain form post goes back to the page."""
    if request.headers.get("hx-request"):
        return None
    target = f"/saved/{run.saved_job_id}#run" if run.saved_job_id else "/profile#run"
    return RedirectResponse(target, status_code=303)


@router.get("/{run_id}/panel", response_class=HTMLResponse)
def run_panel(request: Request, run_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return panel(request, db, user, runs.get(db, user, run_id))


@router.post("/{run_id}/prompts/{prompt_id}", response_class=HTMLResponse)
def answer_prompt(
    request: Request,
    run_id: int,
    prompt_id: int,
    answer: Annotated[str, Form(max_length=runs.ANSWER_MAX + 100)] = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    run = runs.get(db, user, run_id)
    event = db.get(RunEvent, prompt_id)
    if event is None or event.run_id != run.id:
        raise HTTPException(status_code=404)
    try:
        runs.answer(db, run, event, answer)
    except runs.Invalid as error:
        db.rollback()
        return panel(request, db, user, run, error=str(error), status=422)
    db.commit()
    return _back(request, run) or panel(request, db, user, run)


@router.post("/{run_id}/stop", response_class=HTMLResponse)
def stop_run(request: Request, run_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    run = runs.stop(db, runs.get(db, user, run_id))
    db.commit()
    return _back(request, run) or panel(request, db, user, run)


@router.get("/{run_id}/prompts/{prompt_id}/screenshot.png")
def prompt_screenshot(run_id: int, prompt_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    run = runs.get(db, user, run_id)
    event = db.get(RunEvent, prompt_id)
    if event is None or event.run_id != run.id or event.screenshot is None:
        raise HTTPException(status_code=404)
    return Response(event.screenshot, media_type="image/png", headers={"Cache-Control": "private, no-store"})
