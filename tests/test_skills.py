"""The /apply and /profile skills (#66) carry non-negotiables 7, 8 and 9 as
instructions to Claude Code. The web app enforces what it can in code (the
tailored resume's check, the profile's grounding); these tests keep the rules that
only the skill can carry from being edited away."""

import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / ".claude" / "skills"


def skill(name: str) -> tuple[dict, str]:
    text = (SKILLS / name / "SKILL.md").read_text()
    front, body = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S).groups()
    meta = dict(line.split(": ", 1) for line in front.splitlines())
    return meta, body


def test_apply_keeps_its_rules():
    meta, body = skill("apply")
    assert meta["name"] == "apply" and meta["disable-model-invocation"] == "true"  # only when Sean asks
    for rule in (
        "**Never click the final Submit.**",  # non-negotiable 8
        "**Never invent anything.**",  # non-negotiable 7
        "**Sean's to answer**",  # CAPTCHAs, non-negotiable 9
        "**Never LinkedIn Easy Apply, never Indeed.**",
        "**he\n   types the password**",
        "**Never approve without his say-so.**",
    ):
        assert rule in body, rule


def test_profile_extracts_only_what_the_resume_states():
    meta, body = skill("profile")
    assert meta["name"] == "profile" and meta["disable-model-invocation"] == "true"
    assert "extract **only what it states**" in body and "copied character for character" in body
