"""The AI spend ledger and the monthly caps (#22), ported from Orbit Ops's spend.py.

Every Claude call runs on Sean's one key (plan §10, decision 2), so there are two
caps: each user's (AI_MONTHLY_BUDGET_CENTS_PER_USER), and everyone's together
(AI_MONTHLY_BUDGET_CENTS), which bounds the key itself. `run()` refuses before
calling when there is no key or either cap is reached, and records what the call
cost after it. The cost is known only afterwards, so one call can overrun a cap
by at most its own cost. That is accepted.

A user's month is summed over their own rows only (non-negotiable 1). The shared
total is the one sum over every user's rows, and it only ever answers "is the
key's budget spent?", never whose money it was. A month is a calendar month in
APP_TIMEZONE.

The ledger row is written in its own session and committed at once: money spent
stays spent even if the feature's own transaction later rolls back.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import ai
from app.config import get_settings
from app.db import SessionLocal
from app.models import AiSpend

log = logging.getLogger("apply.ai")

_MICROCENTS_PER_CENT = 1_000_000


def _now() -> datetime:
    return datetime.now(UTC)  # a seam: tests pin the clock here


def now() -> datetime:
    """The ledger's clock, for callers outside this module (the admin page)."""
    return _now()


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The calendar month containing `now`, in APP_TIMEZONE, as [start, end)."""
    local = now.astimezone(get_settings().tz)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def _spent(db: Session, now: datetime, user_id: int | None) -> int:
    start, end = month_bounds(now)
    query = select(func.coalesce(func.sum(AiSpend.cost_microcents), 0)).where(
        AiSpend.created_at >= start, AiSpend.created_at < end
    )
    if user_id is not None:
        query = query.where(AiSpend.user_id == user_id)
    return int(db.scalar(query))


def user_spent_this_month(db: Session, user_id: int, now: datetime | None = None) -> int:
    """Microcents this user has spent on AI in `now`'s month."""
    return _spent(db, now or _now(), user_id)


def shared_spent_this_month(db: Session, now: datetime | None = None) -> int:
    """Microcents everyone together has spent in `now`'s month (the key's cap only)."""
    return _spent(db, now or _now(), None)


def user_budget_microcents() -> int:
    return get_settings().ai_monthly_budget_cents_per_user * _MICROCENTS_PER_CENT


def shared_budget_microcents() -> int:
    return get_settings().ai_monthly_budget_cents * _MICROCENTS_PER_CENT


def refusal(db: Session, user_id: int | None, now: datetime | None = None) -> ai.Reason | None:
    """Why a call for this user would be refused right now, or None if it would go
    ahead. The pages use this to say so before anyone clicks."""
    if not get_settings().anthropic_api_key:
        return "no_key"
    now = now or _now()
    if user_id is not None and _spent(db, now, user_id) >= user_budget_microcents():
        return "over_budget"
    if _spent(db, now, None) >= shared_budget_microcents():
        return "over_shared_budget"
    return None


def record(user_id: int | None, purpose: ai.Purpose, model: str, usage: ai.Usage, now: datetime | None = None) -> AiSpend:
    """Write one ledger row, committed in a session of its own."""
    row = AiSpend(
        user_id=user_id,
        purpose=purpose,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cost_microcents=ai.cost_microcents(model, usage),
    )
    if now is not None:
        row.created_at = now
    with SessionLocal() as db:
        db.add(row)
        db.commit()
    return row


def run(user_id: int | None, purpose: ai.Purpose, call: Callable, *, now: datetime | None = None):
    """Make one cap-checked, recorded model call for `user_id` (None only for work
    no user asked for, which the shared cap alone bounds). `call(client, model)` is
    the feature's seam in app/ai.py and returns the SDK's Message. Raises
    AIUnavailable; returns the Message."""
    import anthropic  # pinned; app.ai.client() has already turned its absence into AIUnavailable

    client = ai.client(get_settings().anthropic_api_key)  # AIUnavailable("no_key") first
    with SessionLocal() as db:
        reason = refusal(db, user_id, now)
    if reason is not None:
        raise ai.AIUnavailable(reason)
    model = ai.MODELS[purpose]
    try:
        response = call(client, model)
    except anthropic.APIError as exc:
        # Never the prompt or the reply: they hold a resume or answers.
        log.warning("AI %s call failed: %s (request %s)", purpose, type(exc).__name__, getattr(exc, "request_id", None))
        raise ai.AIUnavailable("error") from exc
    record(user_id, purpose, getattr(response, "model", None) or model, ai.Usage.of(response.usage), now)
    return response
