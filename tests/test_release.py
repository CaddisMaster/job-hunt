"""The release pipeline (#4): the PR checks run for real where they can, and the
workflows' shape is held where they can only run on GitHub."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import check_criteria

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _changelog_script() -> str:
    steps = _workflow("changelog.yml")["jobs"]["changelog"]["steps"]
    return next(step["run"] for step in steps if "CHANGELOG.md" in step.get("run", ""))


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()


def _pr(tmp_path: Path, changed: dict[str, str], labels: str = "[]") -> subprocess.CompletedProcess:
    """A scratch repo with a base commit, then a PR commit changing `changed`;
    runs changelog.yml's step against it the way the workflow does."""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("x = 1\n")
    (repo / "CHANGELOG.md").write_text("## [Unreleased]\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    for path, text in changed.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pr")
    return subprocess.run(
        ["bash", "-e", "-c", _changelog_script()],
        cwd=repo, env={**os.environ, "BASE_SHA": base, "LABELS": labels}, capture_output=True, text=True,
    )


@pytest.mark.criterion(4, "A PR without a CHANGELOG entry fails")
def test_an_app_change_without_a_changelog_entry_fails(tmp_path):
    result = _pr(tmp_path, {"app/main.py": "x = 2\n"})
    assert result.returncode != 0
    assert "changes app/ but not CHANGELOG.md" in result.stdout


def test_an_app_change_with_an_entry_passes(tmp_path):
    assert _pr(tmp_path, {"app/main.py": "x = 2\n", "CHANGELOG.md": "## [Unreleased]\n- x\n"}).returncode == 0


def test_the_skip_changelog_label_passes(tmp_path):
    assert _pr(tmp_path, {"app/main.py": "x = 2\n"}, labels='["skip-changelog"]').returncode == 0


def test_a_change_outside_app_needs_no_entry(tmp_path):
    assert _pr(tmp_path, {"tests/test_x.py": "pass\n"}).returncode == 0


ISSUE = """Some context.

## Acceptance criteria
```gherkin
Scenario: The first thing works
  Then it works

Scenario: The second thing works
  Then it also works
```
"""


def _claimed_tests(tmp_path: Path, *titles: str) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    body = "import pytest\n\n" + "".join(
        f'@pytest.mark.criterion(7, "{title}")\ndef test_{i}():\n    pass\n\n' for i, title in enumerate(titles)
    )
    (tests / "test_claims.py").write_text(body)
    return tmp_path


@pytest.mark.criterion(4, "A PR must claim its issue's scenarios")
def test_an_unclaimed_scenario_fails_the_check_and_is_named(tmp_path):
    lines: list[str] = []
    root = _claimed_tests(tmp_path, "The first thing works")
    assert check_criteria.run("Closes #7", {7: ISSUE}, repo_root=root, out=lines.append) == 1
    assert any("The second thing works" in line and "::error::" in line for line in lines)


def test_every_scenario_claimed_passes(tmp_path):
    root = _claimed_tests(tmp_path, "The first thing works")
    body = 'Closes #7\n\nVerified by hand: #7 "The second thing works" — read it'
    assert check_criteria.run(body, {7: ISSUE}, repo_root=root, out=lambda _: None) == 0


def test_this_repos_own_claims_are_found():
    claims = check_criteria.pytest_claims(ROOT / "tests")
    assert (4, check_criteria.normalize("A PR must claim its issue's scenarios")) in claims


@pytest.mark.criterion(4, "A release waits for approval before deploying")
def test_a_release_builds_pushes_and_waits_for_production_approval():
    workflow = _workflow("release.yml")
    assert workflow[True]["release"]["types"] == ["published"]  # YAML reads `on:` as True
    jobs = workflow["jobs"]
    push = next(step for step in jobs["build-push"]["steps"] if step.get("id") == "push")
    assert push["with"]["push"] is True
    assert "ghcr.io/caddismaster/job-hunt" == workflow["env"]["IMAGE"]
    assert jobs["deploy"]["environment"] == "production"
    assert set(jobs["deploy"]["needs"]) == {"build-push", "smoke", "scan"}
    assert workflow["env"]["APP_DIR"] == "/opt/apply"
    assert workflow["env"]["APP_URL"] == "https://apply.seandesmet.com"


def test_the_smoke_test_boots_the_pushed_image_against_production_postgres():
    jobs = _workflow("release.yml")["jobs"]
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert jobs["smoke"]["services"]["db"]["image"] == compose["services"]["db"]["image"]
    runs = " ".join(step.get("run", "") for step in jobs["smoke"]["steps"])
    assert "alembic upgrade head" in runs and "/healthz" in runs


@pytest.mark.criterion(4, "A bad release can be rolled back")
def test_rollback_redeploys_a_named_version_and_checks_it_serves():
    workflow = _workflow("rollback.yml")
    assert workflow[True]["workflow_dispatch"]["inputs"]["version"]["required"] is True
    job = workflow["jobs"]["rollback"]
    assert job["environment"] == "production"
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert 'git show "v${VERSION}:docker-compose.yml"' in runs
    assert "docker compose up -d --no-deps --remove-orphans web" in runs
    assert '"version\\":\\"${VERSION}\\""' in runs or "${APP_URL}/healthz" in runs
    # A rollback never runs migrations: they're expand-only, so the old image runs on the new schema.
    assert "alembic" not in runs
    # The package is private like the repo, so the existence check authenticates.
    assert workflow["permissions"]["packages"] == "read"


def test_a_release_and_a_rollback_never_interleave():
    assert _workflow("release.yml")["concurrency"]["group"] == _workflow("rollback.yml")["concurrency"]["group"]
