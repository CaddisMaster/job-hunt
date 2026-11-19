"""The admin panel's queries (#3).

⚠️ The one place that names users other than the current one, and only
`require_admin` routes call it. It deliberately shows accounts only — never
anyone's resumes, answers or applications (plan §2): keep it that way.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, new_session_token


def accounts(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


def set_disabled(db: Session, user: User, disabled: bool) -> None:
    if disabled:
        user.disabled_at = datetime.now(UTC)
        user.session_token = new_session_token()  # ends every session it has
    else:
        user.disabled_at = None
