"""The admin panel's queries (#3).

⚠️ The one place that names users other than the current one, and only
`require_admin` routes call it. It deliberately shows accounts only — never
anyone's resumes, answers or applications (plan §2): keep it that way. The
worker's job runs are shared, not anyone's data, and say only counts.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import spend, worker
from app.config import get_settings
from app.models import AiSpend, JobRun, User, new_session_token


def accounts(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


def month_spend(db: Session, now: datetime | None = None) -> dict[int, int]:
    """Microcents each account has spent on AI this month (#22): a total per user,
    never what it was spent on. The month is spend's month."""
    start, end = spend.month_bounds(now or spend.now())
    rows = db.execute(
        select(AiSpend.user_id, func.sum(AiSpend.cost_microcents))
        .where(AiSpend.created_at >= start, AiSpend.created_at < end, AiSpend.user_id.is_not(None))
        .group_by(AiSpend.user_id)
    )
    return {user_id: int(total) for user_id, total in rows}


def set_disabled(db: Session, user: User, disabled: bool) -> None:
    if disabled:
        user.disabled_at = datetime.now(UTC)
        user.session_token = new_session_token()  # ends every session it has
    else:
        user.disabled_at = None


def last_runs(db: Session) -> list[dict]:
    """Each scheduled job (#41), when it runs, and its newest run (None if it never has),
    with times in APP_TIMEZONE."""
    tz = get_settings().tz
    rows = []
    for job in worker.JOBS:
        run = db.scalar(select(JobRun).where(JobRun.job == job.name).order_by(JobRun.started_at.desc()).limit(1))
        rows.append(
            {
                "name": job.name,
                "at": job.at,
                "run": run,
                "started": run.started_at.astimezone(tz) if run else None,
                "seconds": (run.finished_at - run.started_at).total_seconds() if run and run.finished_at else None,
            }
        )
    return rows
