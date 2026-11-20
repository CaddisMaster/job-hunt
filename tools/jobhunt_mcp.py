#!/usr/bin/env python3
"""Job Hunt's bridge as tools for Claude Code (#76): a small MCP server on stdio.

Claude Code starts it (see docs/apply.md) and calls its tools; each tool is one
request to Job Hunt's JSON API (#74) with Sean's personal access token. Standard
library only, so there's nothing to install or pin on the Mac.

    JOBHUNT_URL    where Job Hunt is (default https://apply.seandesmet.com)
    JOBHUNT_TOKEN  the token, for testing only. Normally it's read from the macOS
                   Keychain: security add-generic-password -s jobhunt-token -a "$USER" -w

The token never appears in a tool's result or in an error. The tailored PDF is
saved to a private temporary folder so the browser can upload it.

MCP over stdio: one JSON-RPC 2.0 message per line in, one per line out. Only what
Claude Code needs is implemented: initialize, ping, tools/list and tools/call.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

VERSION = "0.1.0"
DEFAULT_URL = "https://apply.seandesmet.com"
KEYCHAIN_SERVICE = "jobhunt-token"
TIMEOUT = 60


class ToolError(Exception):
    """A tool failed; the message is shown to Claude, so it never holds the token."""


# ---- Talking to Job Hunt ---------------------------------------------------------------


def _token() -> str:
    token = os.environ.get("JOBHUNT_TOKEN", "").strip()
    if token:
        return token
    try:
        found = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        found = None
    if found is None or found.returncode != 0 or not found.stdout.strip():
        raise ToolError(
            f"No Job Hunt token. Make one on Job Hunt's Profile page, then: "
            f'security add-generic-password -s {KEYCHAIN_SERVICE} -a "$USER" -w'
        )
    return found.stdout.strip()


def _request(method: str, path: str, body: object = None) -> tuple[int, dict[str, str], bytes]:
    """One request to the API (tests replace this): (status, headers, body)."""
    base = os.environ.get("JOBHUNT_URL", DEFAULT_URL).rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Authorization", f"Bearer {_token()}")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", f"jobhunt-mcp/{VERSION}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in (error.headers or {}).items()}, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ToolError(f"Job Hunt couldn't be reached ({type(error).__name__}).") from None


def _call(method: str, path: str, body: object = None) -> object:
    status, _, raw = _request(method, path, body)
    if status == 204:
        return None
    try:
        answer = json.loads(raw) if raw else None
    except ValueError:
        answer = None
    if status >= 400:
        message = answer.get("error") if isinstance(answer, dict) else None
        detail = answer.get("fields") if isinstance(answer, dict) else None
        raise ToolError(f"Job Hunt answered {status}: {message or 'request failed'}" + (f" {detail}" if detail else ""))
    return answer


# ---- The tools ---------------------------------------------------------------------------


def _job_id(args: dict) -> int:
    job_id = args.get("job_id")
    if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 1:
        raise ToolError("job_id must be a saved job's number.")
    return job_id


def list_jobs(args: dict) -> object:
    return _call("GET", "/api/jobs")


def next_job(args: dict) -> object:
    return _call("GET", "/api/apply/next") or {"next": None, "message": "Nothing is ready to apply to."}


def get_job_package(args: dict) -> object:
    return _call("GET", f"/api/jobs/{_job_id(args)}/package")


def get_resume_and_profile(args: dict) -> object:
    return _call("GET", "/api/profile")


def send_profile(args: dict) -> object:
    return _call("POST", "/api/profile", args.get("profile") or {})


def send_tailored_resume(args: dict) -> object:
    return _call("POST", f"/api/jobs/{_job_id(args)}/tailored", args.get("resume") or {})


def approve_tailored_resume(args: dict) -> object:
    return _call("POST", f"/api/jobs/{_job_id(args)}/tailored/approve")


def download_resume_pdf(args: dict) -> object:
    job_id = _job_id(args)
    status, headers, raw = _request("GET", f"/api/jobs/{job_id}/resume.pdf")
    if status != 200 or not raw.startswith(b"%PDF"):
        raise ToolError(f"Job Hunt answered {status}: no tailored resume PDF for job {job_id}.")
    name = "Resume.pdf"
    disposition = headers.get("content-disposition", "")
    if 'filename="' in disposition:
        name = Path(disposition.split('filename="', 1)[1].split('"', 1)[0]).name or name
    folder = Path(tempfile.mkdtemp(prefix=f"jobhunt-{job_id}-"))  # private to this user (0700)
    path = folder / name
    path.write_bytes(raw)
    return {"path": str(path), "approved": headers.get("x-approved") == "true", "bytes": len(raw)}


def add_answer(args: dict) -> object:
    body = {k: args[k] for k in ("key", "question", "answer") if isinstance(args.get(k), str)}
    return _call("POST", "/api/answers", body)


def record_application(args: dict) -> object:
    body: dict = {
        "outcome": args.get("outcome"),
        "note": args.get("note") or "",
        "answers_used": args.get("answers_used") or [],
    }
    if args.get("screenshot_path"):
        shot = Path(os.path.expanduser(str(args["screenshot_path"])))
        if not shot.is_file():
            raise ToolError(f"No screenshot at {shot}.")
        body["screenshot_png_base64"] = base64.b64encode(shot.read_bytes()).decode()
    return _call("POST", f"/api/jobs/{_job_id(args)}/application", body)


def get_site_note(args: dict) -> object:
    return _call("GET", f"/api/site-notes/{quote(str(args.get('host', '')), safe='')}")


def save_site_note(args: dict) -> object:
    return _call("PUT", f"/api/site-notes/{quote(str(args.get('host', '')), safe='')}", {"note": args.get("note", "")})


_JOB = {"job_id": {"type": "integer", "description": "The saved job's number in Job Hunt."}}
_ROLE_ITEM = {"type": "object", "properties": {"value": {"type": "string"}, "source": {"type": "string", "description": "The exact resume line it comes from, copied verbatim."}}, "required": ["value", "source"]}  # fmt: skip

TOOLS: dict[str, tuple] = {
    "list_jobs": (list_jobs, "List my saved jobs in Job Hunt, newest first, with whether each is ready to apply to.", {}, []),
    "next_job": (next_job, "The next job to apply to: the oldest one with an approved tailored resume and nothing recorded yet.", {}, []),
    "get_job_package": (get_job_package, "Everything for one job: the posting, my resume's text, my confirmed profile, my answer bank, the tailored resume if any, and Claude's note for the job's site.", _JOB, ["job_id"]),
    "get_resume_and_profile": (get_resume_and_profile, "My base resume's text and my current profile (and whether I've confirmed it), to build or rebuild the profile from.", {}, []),
    "send_profile": (send_profile, "Send what was read from my resume, to be checked against it and stored as my profile (I confirm it on the website). Every item must quote its exact resume line in `source`.", {"profile": {"type": "object", "description": "roles: [{title, employer, start, end, source}], skills/education/certifications: [{value, source}], total_years, seniority (entry|mid|senior|lead|executive|unknown), target_titles: [str]", "properties": {"roles": {"type": "array"}, "skills": {"type": "array", "items": _ROLE_ITEM}, "education": {"type": "array", "items": _ROLE_ITEM}, "certifications": {"type": "array", "items": _ROLE_ITEM}}}}, ["profile"]),
    "send_tailored_resume": (send_tailored_resume, "Send a tailored resume for one job: which of my resume's lines to use, in order. Job Hunt checks every line against my resume and confirmed profile, makes the PDF, and says what it left out and why. Copy lines verbatim; choose roles by role_number (1 = the profile's first role).", {**_JOB, "resume": {"type": "object", "description": "name, contact: [str], summary: [str], roles: [{role_number, bullets: [str]}], skills: [str], education: [str], certifications: [str]"}}, ["job_id", "resume"]),
    "approve_tailored_resume": (approve_tailored_resume, "Approve the job's tailored resume, only after I've said so: it's then the one used to apply.", _JOB, ["job_id"]),
    "download_resume_pdf": (download_resume_pdf, "Save the job's tailored resume PDF to a temporary file and return its path, for uploading in the browser.", _JOB, ["job_id"]),
    "add_answer": (add_answer, "Add an answer I gave during an application to my answer bank, only with my OK: a starter question by `key`, or a new one by `question`.", {"key": {"type": "string"}, "question": {"type": "string"}, "answer": {"type": "string"}}, ["answer"]),
    "record_application": (record_application, "Record how applying went: outcome submitted (only after I clicked Submit myself) or stopped (say why in note), the answers used, and a screenshot of the confirmation page (a PNG file path).", {**_JOB, "outcome": {"type": "string", "enum": ["submitted", "stopped"]}, "note": {"type": "string"}, "answers_used": {"type": "array", "items": {"type": "object", "properties": {"question": {"type": "string"}, "answer": {"type": "string"}}, "required": ["question", "answer"]}}, "screenshot_path": {"type": "string"}}, ["job_id", "outcome"]),
    "get_site_note": (get_site_note, "What was learned before about a site's application flow (by host, e.g. pnc.wd5.myworkdayjobs.com).", {"host": {"type": "string"}}, ["host"]),
    "save_site_note": (save_site_note, "Save what was learned about a site's application flow, replacing the old note, so the next application there is faster.", {"host": {"type": "string"}, "note": {"type": "string"}}, ["host", "note"]),
}  # fmt: skip


# ---- MCP over stdio ----------------------------------------------------------------------


def _tool_list() -> list[dict]:
    return [
        {"name": name, "description": desc, "inputSchema": {"type": "object", "properties": props, "required": required}}
        for name, (_, desc, props, required) in TOOLS.items()
    ]


def handle(message: dict) -> dict | None:
    """One JSON-RPC message in, its response out (None for a notification)."""
    method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
    if msg_id is None:
        return None  # notifications (initialized, cancelled) need no answer

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "jobhunt", "version": VERSION},
        })  # fmt: skip
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tool_list()})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return ok({"content": [{"type": "text", "text": f"No tool called {name}."}], "isError": True})
        try:
            result = TOOLS[name][0](args if isinstance(args, dict) else {})
        except ToolError as error:
            return ok({"content": [{"type": "text", "text": str(error)}], "isError": True})
        return ok({"content": [{"type": "text", "text": json.dumps(result, indent=1)}], "isError": False})
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(message) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
