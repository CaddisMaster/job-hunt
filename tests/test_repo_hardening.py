"""Tests of the repository's own configuration (ported from Orbit Ops). Each one
guards a rule that is easy to break in a one-line diff and silent when broken."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _requirement_lines(name: str) -> list[str]:
    lines = (ROOT / name).read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _services():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


class _ComposeLoader(yaml.SafeLoader):
    """safe_load plus compose's merge tags (`!override`, `!reset`), read as their plain value."""


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(
        _tag,
        lambda loader, node: (
            loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
            else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
            else loader.construct_scalar(node)
        ),
    )


def _dev_web():
    return yaml.load((ROOT / "docker-compose.override.yml").read_text(), Loader=_ComposeLoader)["services"]["web"]


def _dockerfile_instructions():
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _ci():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())


def test_every_dependency_is_pinned_exactly():
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in _requirement_lines(name):
            assert re.fullmatch(r"[A-Za-z0-9_.\-\[\]]+==[A-Za-z0-9_.\-+]+", line), f"{name}: not an exact pin: {line}"


def test_dockerfile_ships_the_prod_stage_and_pins_the_base_by_digest():
    dockerfile = (ROOT / "Dockerfile").read_text()
    stages = re.findall(r"^FROM .* AS (\w+)$", dockerfile, flags=re.MULTILINE)
    assert stages[-1] == "prod", "an untargeted build must produce the prod stage"
    assert re.search(r"^FROM python:[\d.]+-slim@sha256:[0-9a-f]{64} AS base$", dockerfile, flags=re.MULTILINE)
    assert "\nUSER appuser\n" in dockerfile


def test_root_owns_the_code_and_ci_checks_the_built_image():
    dockerfile = _dockerfile_instructions()
    assert "--chown" not in dockerfile, "the app user must not own anything it runs"
    assert "postgresql-client" not in dockerfile
    steps = _ci()["jobs"]["docker-build"]["steps"]
    check = next(step["run"] for step in steps if step.get("name") == "Check the image's user and ownership")
    assert 'test "$(whoami)" = appuser' in check
    assert "find /app -writable" in check


@pytest.mark.criterion(1, "Postgres is not reachable from the host")
def test_compose_publishes_only_to_localhost_and_never_the_database():
    services = _services()
    assert "ports" not in services["db"]
    for name, service in services.items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} beyond localhost (ufw is bypassed)"


def test_compose_tag_has_no_default():
    assert "${TAG:?" in _services()["web"]["image"], "TAG must be required, never defaulted (BB #190)"


def test_every_service_caps_its_logs_and_its_memory():
    for name, service in _services().items():
        assert service.get("logging", {}).get("options", {}).get("max-size"), f"{name} has unbounded logs"
        assert service.get("mem_limit"), f"{name} has no memory limit (the droplet is shared)"


def test_the_web_service_is_locked_down():
    web = _services()["web"]
    assert web["read_only"] is True
    assert web["tmpfs"] == ["/tmp"]
    assert web["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in web["security_opt"]
    dev = _dev_web()
    assert "read_only" not in dev and "cap_drop" not in dev and "security_opt" not in dev and "cap_add" not in dev


def test_postgres_is_pinned_and_ci_tests_against_the_same_one():
    image = _services()["db"]["image"]
    assert re.fullmatch(r"postgres:16\.\d+@sha256:[0-9a-f]{64}", image), f"db is not pinned: {image}"
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for ref in re.findall(r"image:\s*(postgres\S*)", workflow.read_text()):
            assert ref == image, f"{workflow.name} tests against {ref}, production runs {image}"


def test_actions_are_pinned_by_commit_sha():
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for uses in re.findall(r"uses:\s*(\S+)", workflow.read_text()):
            if uses.startswith("./"):
                continue
            assert re.search(r"@[0-9a-f]{40}$", uses), f"{workflow.name}: {uses} is not pinned to a commit SHA"


@pytest.mark.criterion(1, "CI runs the test suite")
def test_ci_lints_migrates_round_trips_and_tests():
    jobs = _ci()["jobs"]
    assert {"lint", "test", "docker-build"} <= jobs.keys()
    runs = "\n".join(step.get("run", "") for step in jobs["test"]["steps"])
    assert "alembic upgrade head" in runs
    assert "alembic downgrade base" in runs
    assert "pytest" in runs


def test_env_example_lists_every_setting():
    from app.config import Settings

    example = (ROOT / ".env.example").read_text()
    for field in Settings.model_fields:
        if field in {"app_version", "app_commit", "templates_auto_reload"}:
            continue  # baked into the image, or a dev-only knob
        assert re.search(rf"^{field.upper()}=", example, flags=re.MULTILINE), f".env.example is missing {field.upper()}"


def test_the_image_ships_no_docs_or_secrets():
    ignored = set((ROOT / ".dockerignore").read_text().splitlines())
    assert {".env", ".env.*", "**/*.md", "docs/", "infra/", "backups/"} <= ignored


def _scan_step(steps):
    return next(step for step in steps if step.get("uses", "").startswith("anchore/scan-action@"))


def test_ci_and_the_release_scan_the_image_the_same_way():
    workflows = ROOT / ".github" / "workflows"
    ci = _scan_step(_ci()["jobs"]["docker-build"]["steps"])["with"]
    release_jobs = yaml.safe_load((workflows / "release.yml").read_text())["jobs"]
    release = _scan_step(release_jobs["scan"]["steps"])["with"]
    assert ci["image"] == "job-hunt:ci"
    assert {k: v for k, v in ci.items() if k != "image"} == {k: v for k, v in release.items() if k != "image"}
    # The scanner never shares a job with the token that can push images.
    assert release_jobs["scan"]["permissions"] == {"contents": "read", "packages": "read"}


def test_the_release_smoke_test_runs_the_image_locked_down():
    workflows = ROOT / ".github" / "workflows"
    smoke = yaml.safe_load((workflows / "release.yml").read_text())["jobs"]["smoke"]["steps"]
    runs = {step.get("name"): step.get("run", "") for step in smoke}
    lockdown = re.search(r'LOCKDOWN=([^"]+)"', runs["Write the smoke env"]).group(1).split()
    assert ("--pids-limit", str(_services()["web"]["pids_limit"])) in set(zip(lockdown, lockdown[1:], strict=False))
    for name in ("Migrations apply from the pushed image", "Run the pushed image"):
        assert "$LOCKDOWN" in runs[name]
