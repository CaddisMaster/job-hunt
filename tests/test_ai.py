"""#22: what a call costs, the ledger, and the two monthly caps (each user's, and the key's).

No test reaches Anthropic. A feature's seam is played by `Seam`, which returns a
stand-in Message or raises the SDK's own error. The shared cap is summed over
every user, so tests running in parallel would see each other's spend: each test
works in a past year of its own (through `now` / `spend._now`) and clears that
year afterwards. Ported from Orbit Ops's tests/test_ai.py.
"""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from sqlalchemy import delete, select

from app import admin, ai, spend
from app.config import get_settings
from app.db import SessionLocal
from app.models import AiSpend

CENT = 1_000_000  # microcents


def _at(year: int, month: int = 6, day: int = 15, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.fixture
def year(request):
    """A year no other test uses, cleared afterwards. Tests name theirs with @pytest.mark.parametrize."""
    y = request.param
    yield y
    with SessionLocal() as db:
        db.execute(delete(AiSpend).where(AiSpend.created_at >= _at(y - 1, 12, 1), AiSpend.created_at < _at(y + 1, 2, 1)))
        db.commit()


@pytest.fixture
def ai_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-not-a-key")
    monkeypatch.setattr(settings, "ai_monthly_budget_cents_per_user", 200)
    monkeypatch.setattr(settings, "ai_monthly_budget_cents", 1000)
    monkeypatch.setattr(settings, "app_timezone", "UTC")


class Seam:
    """Plays a feature's `_call_*_model(client, model)`, remembering whether it was called."""

    def __init__(self, input_tokens=1000, output_tokens=200, error=None):
        self.calls: list[str] = []
        self.usage = SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens, cache_read_input_tokens=None, cache_creation_input_tokens=None
        )
        self.error = error

    def __call__(self, client, model):
        self.calls.append(model)
        if self.error:
            raise self.error
        return SimpleNamespace(model=model, usage=self.usage, content=[])


def _spend(user_id, cost, when):
    with SessionLocal() as db:
        db.add(
            AiSpend(
                user_id=user_id, purpose="profile", model="claude-opus-5-5",
                input_tokens=0, output_tokens=0, cost_microcents=cost, created_at=when,
            )
        )
        db.commit()


def _rows(y):
    with SessionLocal() as db:
        return list(db.scalars(select(AiSpend).where(AiSpend.created_at >= _at(y, 1, 1, 0), AiSpend.created_at < _at(y + 1, 1, 1, 0))))


# --- what a call costs --------------------------------------------------------------
@pytest.mark.parametrize(
    ("model", "usage", "microcents"),
    [
        # 1000 x $0.10 + 200 x $0.50 per MTok = $0.0002 = 0.02 cents
        ("claude-haiku-5-5", ai.Usage(1000, 200), 20_000),
        ("claude-sonnet-5-5", ai.Usage(1000, 200), 400_000),
        ("claude-opus-5-5", ai.Usage(1000, 200), 800_000),
        # Sonnet 5.5's cache reads are $0.10; writes 1.25 x $2.00
        ("claude-sonnet-5-5", ai.Usage(0, 0, cache_read_tokens=10_000, cache_write_tokens=1000), 100_000 + 250_000),
        # Haiku's long-prompt rate: $0.50 / $2.50 once the prompt passes 100K tokens
        ("claude-haiku-5-5", ai.Usage(100_001, 1000), 5_000_050 + 250_000),
        # an unknown model costs Opus prices, never nothing
        ("claude-mystery-9", ai.Usage(1000, 200), 800_000),
        # a fraction of a microcent rounds up
        ("claude-haiku-5-5", ai.Usage(1, 0), 10),
        ("claude-haiku-5-5", ai.Usage(0, 0, cache_read_tokens=1), 1),
    ],
)
def test_a_call_costs_what_anthropic_charges(model, usage, microcents):
    assert ai.cost_microcents(model, usage) == microcents


def test_usage_reads_the_sdk_shape_with_empty_cache_fields():
    usage = SimpleNamespace(input_tokens=5, output_tokens=7, cache_read_input_tokens=None, cache_creation_input_tokens=3)
    assert ai.Usage.of(usage) == ai.Usage(5, 7, cache_read_tokens=0, cache_write_tokens=3)


def test_every_purpose_has_a_priced_model():
    assert all(model in ai.PRICES for model in ai.MODELS.values())


def test_every_refusal_has_a_message_for_the_page():
    assert set(ai.MESSAGES) == {"no_key", "over_budget", "over_shared_budget", "error"}
    for reason in ai.MESSAGES:
        assert ai.AIUnavailable(reason).message == ai.MESSAGES[reason]


# --- the ledger -------------------------------------------------------------------
@pytest.mark.criterion(22, "A call is recorded against the user who made it")
@pytest.mark.parametrize("year", [2101], indirect=True)
def test_a_call_is_recorded_against_its_user(ai_on, make_user, year):
    user = make_user()
    seam = Seam(input_tokens=1000, output_tokens=200)
    spend.run(user.id, "profile", seam, now=_at(year))
    assert seam.calls == ["claude-opus-5-5"]
    [row] = _rows(year)
    assert (row.user_id, row.purpose, row.model) == (user.id, "profile", "claude-opus-5-5")
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens) == (1000, 200, 0, 0)
    assert row.cost_microcents == 800_000


@pytest.mark.parametrize("year", [2102], indirect=True)
def test_spend_is_kept_when_the_feature_rolls_back(ai_on, make_user, year):
    """The row is committed in its own session: the feature's failure can't un-spend it."""
    with SessionLocal() as db:
        spend.run(make_user().id, "profile", Seam(), now=_at(year))
        db.rollback()
    assert len(_rows(year)) == 1


@pytest.mark.parametrize("year", [2103], indirect=True)
def test_an_api_error_is_unavailable_records_nothing_and_logs_no_content(ai_on, make_user, caplog, year):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages", content=b"SECRET RESUME TEXT")
    seam = Seam(error=anthropic.APIConnectionError(message="connection reset", request=request))
    with caplog.at_level(logging.DEBUG), pytest.raises(ai.AIUnavailable) as raised:
        spend.run(make_user().id, "profile", seam, now=_at(year))
    assert raised.value.reason == "error"
    assert _rows(year) == []
    assert "AI profile call failed: APIConnectionError" in caplog.text
    assert "SECRET RESUME TEXT" not in caplog.text


# --- the caps ---------------------------------------------------------------------
@pytest.mark.criterion(22, "A user over their monthly cap is refused before the call")
@pytest.mark.parametrize("year", [2104], indirect=True)
def test_a_user_over_their_cap_is_refused_before_the_call(ai_on, make_user, year):
    user = make_user()
    _spend(user.id, 200 * CENT, _at(year, 6, 1))
    seam = Seam()
    with pytest.raises(ai.AIUnavailable) as raised:
        spend.run(user.id, "profile", seam, now=_at(year, 6, 30))
    assert raised.value.reason == "over_budget"
    assert raised.value.message == "You've reached this month's AI budget. It resets on the 1st."
    assert seam.calls == []
    assert len(_rows(year)) == 1  # nothing new recorded


@pytest.mark.parametrize("year", [2105], indirect=True)
def test_one_users_cap_doesnt_stop_another(ai_on, make_user, year):
    spent, fresh = make_user(), make_user()
    _spend(spent.id, 200 * CENT, _at(year, 6, 1))
    seam = Seam()
    spend.run(fresh.id, "profile", seam, now=_at(year, 6, 2))
    assert seam.calls == ["claude-opus-5-5"]


@pytest.mark.criterion(22, "The global cap holds across users")
@pytest.mark.parametrize("year", [2106], indirect=True)
def test_the_shared_cap_holds_across_users(ai_on, make_user, year):
    # Five users at 199 cents each: none over their own cap, 995 cents together.
    for _ in range(5):
        _spend(make_user().id, 199 * CENT, _at(year, 6, 1))
    _spend(None, 5 * CENT, _at(year, 6, 1))  # work no user asked for counts too: 1000 cents
    seam = Seam()
    with pytest.raises(ai.AIUnavailable) as raised:
        spend.run(make_user().id, "profile", seam, now=_at(year, 6, 2))
    assert raised.value.reason == "over_shared_budget"
    assert seam.calls == []


@pytest.mark.criterion(22, "Spend resets at the start of the month")
@pytest.mark.parametrize("year", [2107], indirect=True)
def test_last_months_spend_does_not_count(ai_on, make_user, year):
    user = make_user()
    _spend(user.id, 500 * CENT, _at(year, 5, 31, 23))
    seam = Seam()
    spend.run(user.id, "profile", seam, now=_at(year, 6, 1, 0))
    assert seam.calls == ["claude-opus-5-5"]


@pytest.mark.parametrize("year", [2108], indirect=True)
def test_the_month_is_new_yorks_not_utcs(ai_on, make_user, monkeypatch, year):
    monkeypatch.setattr(get_settings(), "app_timezone", "America/New_York")
    user = make_user()
    # 03:00 UTC on 1 June is still 31 May in New York: same month as the 30 May spend.
    _spend(user.id, 200 * CENT, _at(year, 5, 30))
    with pytest.raises(ai.AIUnavailable):
        spend.run(user.id, "profile", Seam(), now=_at(year, 6, 1, 3))
    # 05:00 UTC on 1 June is 1 June in New York: a new month.
    spend.run(user.id, "profile", Seam(), now=_at(year, 6, 1, 5))


@pytest.mark.parametrize("year", [2109], indirect=True)
def test_a_zero_cap_turns_ai_off(ai_on, make_user, monkeypatch, year):
    monkeypatch.setattr(get_settings(), "ai_monthly_budget_cents_per_user", 0)
    with pytest.raises(ai.AIUnavailable) as raised:
        spend.run(make_user().id, "profile", Seam(), now=_at(year))
    assert raised.value.reason == "over_budget"


@pytest.mark.parametrize("setting", ["AI_MONTHLY_BUDGET_CENTS", "AI_MONTHLY_BUDGET_CENTS_PER_USER"])
def test_a_cap_refuses_a_negative_value(monkeypatch, setting):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv(setting, "-1")
    with pytest.raises(ValidationError):
        Settings()


# --- privacy ----------------------------------------------------------------------
@pytest.mark.criterion(22, "One user's spend is invisible to another")
@pytest.mark.parametrize("year", [2110], indirect=True)
def test_one_users_spend_is_invisible_to_another(ai_on, make_user, client, year):
    from tests.conftest import login

    mine, theirs = make_user(), make_user()
    _spend(theirs.id, 150 * CENT, _at(year))
    with SessionLocal() as db:
        assert spend.user_spent_this_month(db, mine.id, _at(year)) == 0
        assert spend.user_spent_this_month(db, theirs.id, _at(year)) == 150 * CENT
    # The only page with spend on it is the admin panel, and only admins reach it.
    login(client, mine.email)
    assert client.get("/admin").status_code == 404
    assert "1.50" not in client.get("/").text


@pytest.mark.parametrize("year", [2111], indirect=True)
def test_the_admin_sees_a_total_per_account_and_nothing_else(ai_on, make_user, client, monkeypatch, year):
    from tests.conftest import login

    me, them = make_user(is_admin=True), make_user()
    _spend(them.id, 150 * CENT, _at(year))
    monkeypatch.setattr(spend, "_now", lambda: _at(year))
    with SessionLocal() as db:
        assert admin.month_spend(db)[them.id] == 150 * CENT
    login(client, me.email)
    page = client.get("/admin").text
    assert "$1.50" in page and "of $2.00" in page
    assert "claude-" not in page  # a total, never which calls or models


# --- no key -----------------------------------------------------------------------
@pytest.mark.criterion(22, "No API key means AI is off, not an error")
def test_no_key_means_ai_is_off_not_an_error(monkeypatch, make_user, caplog):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    seam = Seam()
    with caplog.at_level(logging.DEBUG), pytest.raises(ai.AIUnavailable) as raised:
        spend.run(make_user().id, "profile", seam)
    assert raised.value.reason == "no_key"
    assert raised.value.message == "AI features are off on this server."
    assert seam.calls == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR or r.exc_info]
    with SessionLocal() as db:
        assert spend.refusal(db, None) == "no_key"
