"""The admin panel's queries (#3).

⚠️ The one place that names users other than the current one, and only
`require_admin` routes call it. It deliberately shows accounts only — never
anyone's resumes, answers or applications (plan §2): keep it that way.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import spend
from app.models import AiSpend, User, new_session_token


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
