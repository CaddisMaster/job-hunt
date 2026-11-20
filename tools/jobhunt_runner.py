#!/usr/bin/env python3
"""The runner (#88): takes the runs Sean queues in the web app and does them with
Claude Code, headless, on the VM. A user service (tools/systemd/, installed by
tools/apply-services.sh); standard library only.

The web app can't reach the VM, so this asks it: every few seconds it claims the
oldest queued run through the bridge (one at a time). For each run it starts

    claude -p "<the run's fixed instruction>"
      --append-system-prompt-file <the skill's text>
      --mcp-config <jobhunt + playwright only> --strict-mcp-config
      --allowedTools mcp__jobhunt mcp__playwright

so the run has exactly two sets of tools: Job Hunt's bridge and the browser. The
instruction is built here from the run's kind and number; nothing typed in the web
app reaches the command line. Sean answers whatever it needs on the job's page
(the jobhunt tools' ask_sean, needs_browser, ready_to_submit).

When Claude Code exits, the run is finished as failed if it didn't finish itself, so
a crash never leaves it hanging. On start, anything still running from before is
failed (the runner restarted).
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import jobhunt_mcp as bridge  # noqa: E402  (the same token, URL and error handling)

POLL_SECONDS = 5
RUN_TIMEOUT = 90 * 60  # seconds: a run that's still going after this is ended
PLAYWRIGHT_MCP = "@playwright/mcp@0.0.84"  # pinned, docs/apply.md
CHROMIUM = Path.home() / ".cache/ms-playwright/chromium-1252/chrome-linux-arm64/chrome"
DATA = Path.home() / ".local/share/jobhunt"
SKILLS = REPO / ".claude" / "skills"
SCREEN_UNITS = ("jobhunt-xvfb.service", "jobhunt-vnc.service")

log = logging.getLogger("jobhunt-runner")
stopping = False


def instruction(run: dict) -> tuple[str, Path]:
    """The fixed instruction for a run, and the skill that goes with it. Only the
    run's kind and numbers are used: never text from the web app."""
    run_id = int(run["id"])
    if run["kind"] == "apply":
        return (
            f"Web-driven run {run_id}: apply to saved job {int(run['job_id'])}. "
            "Follow the apply skill in your instructions, in its web mode.",
            SKILLS / "apply" / "SKILL.md",
        )
    if run["kind"] == "profile":
        return (
            f"Web-driven run {run_id}: build my profile from my resume. "
            "Follow the profile skill in your instructions, in its web mode.",
            SKILLS / "profile" / "SKILL.md",
        )
    raise ValueError(f"unknown run kind {run['kind']!r}")


def skill_text(path: Path) -> str:
    """The skill's body, without its front matter."""
    text = path.read_text()
    if text.startswith("---\n"):
        text = text.split("\n---\n", 1)[1]
    return text


def mcp_config() -> dict:
    env = {"JOBHUNT_URL": os.environ.get("JOBHUNT_URL", bridge.DEFAULT_URL)}
    return {
        "mcpServers": {
            "jobhunt": {"command": sys.executable, "args": [str(HERE / "jobhunt_mcp.py")], "env": env},
            "playwright": {
                "command": "npx",
                "args": [
                    "-y", PLAYWRIGHT_MCP, "--browser", "chrome", "--executable-path", str(CHROMIUM),
                    "--user-data-dir", str(DATA / "browser"), "--output-dir", str(DATA / "screenshots"),
                    "--viewport-size", "1400x850",
                ],
                "env": {"DISPLAY": ":99"},
            },
        }
    }  # fmt: skip


def command(prompt: str, config: Path, system: Path) -> list[str]:
    return [
        shutil.which("claude") or "claude", "-p", prompt,
        "--append-system-prompt-file", str(system),
        "--mcp-config", str(config), "--strict-mcp-config",
        "--allowedTools", "mcp__jobhunt", "mcp__playwright",
        "--output-format", "text",
    ]  # fmt: skip


def screen_up() -> bool:
    for unit in SCREEN_UNITS:
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], check=False).returncode != 0:
            return False
    return True


def finish(run_id: int, status: str, summary: str) -> None:
    try:
        bridge._call("POST", f"/api/runs/{run_id}/finish", {"status": status, "summary": summary})
    except bridge.ToolError as error:
        log.warning("couldn't finish run %s: %s", run_id, error)


def do(run: dict) -> None:
    run_id = int(run["id"])
    log.info("run %s: %s%s", run_id, run["kind"], f" job {run['job_id']}" if run.get("job_id") else "")
    if run["kind"] == "apply" and not screen_up():
        finish(run_id, "failed", "The browser's screen isn't running on the VM: tools/apply-services.sh restart.")
        return
    prompt, skill = instruction(run)
    with tempfile.TemporaryDirectory(prefix=f"jobhunt-run-{run_id}-") as tmp:
        config, system = Path(tmp) / "mcp.json", Path(tmp) / "skill.md"
        config.write_text(json.dumps(mcp_config()))
        system.write_text(skill_text(skill))
        try:
            done = subprocess.run(
                command(prompt, config, system), cwd=REPO, capture_output=True, text=True,
                timeout=RUN_TIMEOUT, check=False,
            )  # fmt: skip
            outcome = f"Claude Code ended (exit {done.returncode}) without finishing the run."
            if done.returncode != 0:
                log.warning("run %s: claude exited %s: %s", run_id, done.returncode, (done.stderr or "")[-500:])
        except subprocess.TimeoutExpired:
            outcome = "The run took longer than 90 minutes, so it was ended."
        except OSError as error:
            outcome = f"Claude Code couldn't be started ({type(error).__name__})."
    state = bridge._call("GET", f"/api/runs/{run_id}")
    if isinstance(state, dict) and state.get("status") == "running":
        finish(run_id, "stopped" if state.get("stop_requested") else "failed", outcome)
    log.info("run %s: finished", run_id)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, lambda *_: globals().update(stopping=True))
    try:
        abandoned = bridge._call("POST", "/api/runs/abandon")
        left = abandoned.get("failed", 0) if isinstance(abandoned, dict) else 0
        log.info("started; %s left over from before", left)
    except bridge.ToolError as error:
        log.warning("couldn't reach Job Hunt yet: %s", error)
    while not stopping:
        try:
            claimed = bridge._call("POST", "/api/runs/claim")
            run = claimed if isinstance(claimed, dict) else None
        except bridge.ToolError as error:
            log.warning("%s", error)
            run = None
        if run:
            do(run)
            continue
        time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
