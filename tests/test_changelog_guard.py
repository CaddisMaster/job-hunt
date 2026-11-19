"""#13 — the Claude Code harness in `.claude/`: the changelog Stop hook, driven end to end in a
scratch repository (the dev image has git, for tests/test_release.py), plus the settings and
/wrap it ships with.

`.claude/` is excluded from the image by `.dockerignore`, so these skip outside the dev container.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLAUDE = ROOT / ".claude"
HOOK = CLAUDE / "hooks" / "changelog-guard.sh"

pytestmark = pytest.mark.skipif(not HOOK.exists(), reason=".claude/ is not in the shipped image")

STOP = json.dumps({
    "session_id": "s", "transcript_path": "/tmp/t.jsonl", "cwd": "/repo",
    "permission_mode": "default", "hook_event_name": "Stop",
    "stop_hook_active": False, "last_assistant_message": "done",
})


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def _repo(tmp_path: Path, changed: dict[str, str]) -> Path:
    """A scratch repo with `main`, then a branch that changes `changed` (left uncommitted:
    the hook reads the working tree, untracked files included)."""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("x = 1\n")
    (repo / "CHANGELOG.md").write_text("## [Unreleased]\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "99-work")
    for path, text in changed.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    return repo


def _stop(repo: Path, payload: str = STOP, **env: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k != "SKIP_CHANGELOG_GUARD"}
    return subprocess.run(["bash", str(HOOK)], cwd=repo, input=payload, capture_output=True,
                          text=True, timeout=30, env={**clean, **env})


@pytest.mark.criterion(13, "A missing changelog entry is caught locally")
def test_an_app_change_without_a_changelog_entry_blocks_once(tmp_path):
    repo = _repo(tmp_path, {"app/main.py": "x = 2\n", "app/new.py": "y = 1\n"})

    first = _stop(repo)
    assert first.returncode == 2
    assert "CHANGELOG guard: this branch changes app/ but not CHANGELOG.md." in first.stderr
    assert "app/main.py" in first.stderr and "app/new.py" in first.stderr

    # Claude Code says so on the next stop; the hook has had its say.
    again = _stop(repo, json.dumps({"stop_hook_active": True}))
    assert again.returncode == 0 and again.stderr == ""


def test_a_changelog_entry_satisfies_it(tmp_path):
    repo = _repo(tmp_path, {"app/main.py": "x = 2\n", "CHANGELOG.md": "## [Unreleased]\n- x\n"})
    assert _stop(repo).returncode == 0


def test_changes_outside_app_and_main_itself_are_left_alone(tmp_path):
    assert _stop(_repo(tmp_path / "a", {"docs/x.md": "x\n"})).returncode == 0

    repo = _repo(tmp_path / "b", {"app/main.py": "x = 2\n"})
    _git(repo, "checkout", "-q", "main")
    assert _stop(repo).returncode == 0


def test_the_local_escape_hatch(tmp_path):
    repo = _repo(tmp_path, {"app/main.py": "x = 2\n"})
    assert _stop(repo, SKIP_CHANGELOG_GUARD="1").returncode == 0


@pytest.mark.criterion(13, "The hook fails open")
@pytest.mark.parametrize("payload", ["", "garbage", "[]", "null", "{}", '{"stopHookActive": false}'])
def test_input_it_cannot_parse_blocks_nothing(tmp_path, payload):
    repo = _repo(tmp_path, {"app/main.py": "x = 2\n"})
    result = _stop(repo, payload)
    assert result.returncode == 0 and result.stderr == ""


def test_no_git_blocks_nothing(tmp_path):
    repo = _repo(tmp_path, {"app/main.py": "x = 2\n"})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "cat", "python3"):  # everything the hook needs except git
        (bin_dir / tool).symlink_to(shutil.which(tool))
    result = _stop(repo, PATH=str(bin_dir))
    assert result.returncode == 0 and result.stderr == ""


def test_not_a_repository_blocks_nothing(tmp_path):
    assert _stop(tmp_path).returncode == 0


def _settings() -> dict:
    return json.loads((CLAUDE / "settings.json").read_text())


@pytest.mark.criterion(13, "Secrets stay unreadable")
def test_settings_deny_reading_env_and_force_pushing():
    deny = _settings()["permissions"]["deny"]
    assert "Read(./.env)" in deny
    assert "Bash(git push --force*)" in deny and "Bash(git push -f*)" in deny


def test_settings_wire_the_stop_hook():
    commands = [h["command"] for group in _settings()["hooks"]["Stop"] for h in group["hooks"]]
    assert commands == ["$CLAUDE_PROJECT_DIR/.claude/hooks/changelog-guard.sh"]
    assert os.access(HOOK, os.X_OK), "the hook must be executable"


@pytest.mark.criterion(13, "The wrap-up command exists")
def test_wrap_describes_job_hunts_sequence():
    wrap = (CLAUDE / "commands" / "wrap.md").read_text()
    assert wrap.startswith("---\ndescription:")
    assert "localhost:5004" in wrap and "localhost:5002" not in wrap
    assert "docs/status.md" in wrap
    assert "git checkout --ours" in wrap, "the stacked-branch recipe (no force-push) is gone"
    allowed = " ".join(_settings()["permissions"]["allow"])
    assert "5002" not in allowed
