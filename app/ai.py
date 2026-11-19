"""Every Claude call the app makes goes through here (#22), ported from Orbit Ops's ai.py.

This module is pure: which model each purpose uses, what a call costs, how to
build a client, and what to tell the user when a call can't happen. It never
touches the database. The ledger and the caps are app/spend.py, and every call
goes through `spend.run()`, which checks both caps first.

Each feature adds its own `_call_*_model(client, model)` seam here (the profile
builder, #24), so a test replaces one function per feature and never reaches
Anthropic. A missing key, a missing package or an API error becomes
`AIUnavailable`, and the page says what happened instead of erroring
(non-negotiable 3).

⚠️ Prompts and replies hold resumes and answers: never log them, and never put
them in the ledger. The ledger records what a call was for and what it cost.
"""

import math
from dataclasses import dataclass
from typing import Literal

Purpose = Literal["profile"]

# One model per purpose. The profile is built once per resume upload and is the
# basis everything later is checked against, so it gets the default model.
MODELS: dict[str, str] = {
    "profile": "claude-opus-5-5",
}


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens. Cache writes are 1.25x input (the 5-minute TTL)."""

    input: float
    output: float
    cache_read: float

    @property
    def cache_write(self) -> float:
        return self.input * 1.25


# Anthropic's first-party prices, checked 2026-11-19. Haiku 5.5's rise above a
# 100K-token prompt.
PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20),
}
# What a model this table doesn't know is charged: the dearest current model
# here. Overestimating spend only turns AI off early; underestimating breaks the cap.
_UNKNOWN = "claude-opus-5-5"
_HAIKU_LONG = Price(input=0.50, output=2.50, cache_read=0.05)
_HAIKU_LONG_ABOVE = 100_000

# Microcents (a millionth of a cent) per (dollar per million tokens) per token:
# $1/MTok = 100 cents / 1e6 tokens = 100 microcents per token.
_MICROCENTS_PER_DOLLAR_PER_MTOK = 100

Reason = Literal["no_key", "over_budget", "over_shared_budget", "error"]

# What the page says for each reason. Plain, and never the exception itself.
MESSAGES: dict[str, str] = {
    "no_key": "AI features are off on this server.",
    "over_budget": "You've reached this month's AI budget. It resets on the 1st.",
    "over_shared_budget": "AI features are paused for everyone until the 1st: this month's shared budget is spent.",
    "error": "Claude couldn't be reached just now. Nothing was charged; try again in a minute.",
}


class AIUnavailable(Exception):
    """No model call happened, or none came back. `reason` says why; `message` is
    what the page shows. Callers degrade; they never show the exception raw."""

    def __init__(self, reason: Reason):
        super().__init__(reason)
        self.reason = reason

    @property
    def message(self) -> str:
        return MESSAGES[self.reason]


@dataclass(frozen=True)
class Usage:
    """What a response's `usage` reports, normalised (the SDK's cache fields can be None)."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def of(cls, usage) -> Usage:
        return cls(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
        )


def cost_microcents(model: str, usage: Usage) -> int:
    """What a call cost, rounded up to a whole microcent."""
    price = PRICES.get(model, PRICES[_UNKNOWN])
    prompt = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    if model == "claude-haiku-5-5" and prompt > _HAIKU_LONG_ABOVE:
        price = _HAIKU_LONG
    dollars_per_mtok_tokens = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return math.ceil(round(dollars_per_mtok_tokens * _MICROCENTS_PER_DOLLAR_PER_MTOK, 6))


def client(api_key: str):
    """An Anthropic client, or AIUnavailable("no_key"). Imported here so a broken
    or missing package costs the AI features, never the app's startup."""
    if not api_key:
        raise AIUnavailable("no_key")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - it's pinned in requirements.txt
        raise AIUnavailable("error") from exc
    # Someone is waiting on the page: fail in a couple of minutes at most, not the
    # SDK's 10. A feature with a longer call passes its own timeout per request.
    return anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=1)
