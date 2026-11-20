# Job Hunt

Sean's job application helper at **apply.seandesmet.com**, for him alone (since 2026-11-20).
He pastes links to jobs he found; Job Hunt saves each posting, tailors his resume to it (a PDF),
and Playwright fills in the application. A person always clicks the final Submit. No crawling and
no ranking. The multi-user plumbing built in 0.1–0.2 stays. FastAPI + Postgres in Docker, the
third tenant on the droplet shared with Budget Buddy and Orbit Ops. Built by copying Orbit Ops.

> ⚠️ **This file is the always-loaded core and is kept small.** Detail lives in `docs/` and is
> *not* loaded automatically — **read the relevant file before acting.**
>
> | Before you… | Read |
> |---|---|
> | start a session — reconcile against `git log` / `gh issue list` | [`docs/status.md`](docs/status.md) |
> | plan the next milestone or a feature's shape | [`docs/plan.md`](docs/plan.md) |
> | write or change anything under `tests/` | [`docs/testing.md`](docs/testing.md) |
> | add an Alembic revision, add an env var, or cut a release | [`CONTRIBUTING.md`](CONTRIBUTING.md) §4, [`VERSIONING.md`](VERSIONING.md) |
> | run anything on the droplet, or change `infra/` | [`RUNBOOK.md`](RUNBOOK.md), [`infra/README.md`](infra/README.md) |
> | copy something from Orbit Ops | that repo's `docs/gotchas.md` — its lessons apply here |

## Tech stack

- **Backend:** Python 3.15, FastAPI (sync routes), SQLAlchemy 2.0 ORM, psycopg 3, Alembic,
  pydantic-settings, argon2-cffi, Starlette sessions. One uvicorn process.
- **Frontend:** Jinja2 + htmx 2.0.4 (vendored, 0BSD, licence beside it in `app/static/`), plain
  CSS with light and dark tokens. No build step.
- **Database:** PostgreSQL 16, its own container, never published to the host.
- **Infra:** Docker Compose, ghcr.io, GitHub Actions, host Nginx + Certbot (shared droplet).

## Project map

```
app/main.py          create_app(): middleware, routers, exception handlers
app/config.py        Settings — every env var, typed. Add new ones here AND .env.example
app/db.py            engine, SessionLocal, get_db dependency, Base
app/models.py        ORM models
app/security.py      argon2 passwords, sessions (require_user, require_admin), CSRF, CSP + security headers
app/rate_limit.py    the Postgres-backed fixed-window limiter (login)
app/invites.py       one-time set-password links (invite, and reset a forgotten password)
app/ai.py            every Claude call's seam: models, prices, AIUnavailable and its page messages
app/spend.py         the AI spend ledger and the two monthly caps (per user, and the key's); spend.run() makes every call
app/resumes.py       resume uploads: type by content, size and parse limits, text extraction, owner-scoped queries
app/profile.py       the profile from a resume: build (one Claude call), ground every claim in the resume, review
app/answers.py       the answer bank: starter questions, their only defaults (EEO decline, remote off), validation
app/grounding.py     non-negotiable 7 in code: a claim vs confirmed facts → supported / unsupported / needs the user
app/admin.py         the admin panel's queries — accounts and job runs, never anyone's private data
app/worker.py        the `worker` service: scheduled jobs (JOBS: housekeeping), due once a day, advisory-locked, runs in job_runs
app/saved.py         saved jobs (#63): a pasted link → the page's JobPosting (JSON-LD or microdata), or pasted text; SSRF-guarded
app/text.py          markup in, plain text out: plain_text(), tidy(), clip()
app/templating.py    the shared Jinja env (app_version, static_url, pop_flash globals)
app/static_files.py  static_url (?v= per release) and Cache-Control on /static
app/routers/         main.py (dashboard, /healthz), auth.py (login/logout), invite.py (/invite/<token>), admin.py (/admin), profile.py (/profile), saved.py (/saved)
alembic/versions/    migrations (0001 the apply_app role, 0002 users + rate_limits, 0003 invites + disabled_at, … 0008 job_runs, 0009 employers, 0010 postings, 0011 workday, 0012 saved_jobs)
.claude/             /wrap (end of session), the changelog Stop hook, the allowlist — see its README
infra/               the droplet as code: ansible/ (the app role on droplet-infra's pinned host role), systemd/ (backup units), test.sh (LXD)
scripts/             create_user.py (bootstrap the first admin), check_criteria.py (CI), install_compose.sh + backup_db.sh (deploy)
```

## Non-negotiables

1. **Every query that reads a user's private data is scoped to the current user.** Resumes, the
   answer bank, saved jobs and applications all carry `user_id`. The admin module is the only
   exception, and it names its target user explicitly.
2. **Rate limits live in Postgres** (`app/rate_limit.py`), never in process memory: they must hold
   for many users, across restarts.
3. **Never show an exception to the user.** The catch-all handler logs and renders `error.html`.
4. **Inline `<script>` needs `nonce="{{ request.state.csp_nonce }}"`**; never add
   `'unsafe-inline'` or `'unsafe-eval'` to the CSP.
5. **Migrations are expand-only within a release.** One Alembic revision per PR, nothing else in it.
6. **Dependencies are pinned exactly, transitive included**; actions by SHA; base images by digest.
   `tests/test_repo_hardening.py` enforces all three.
7. **The agent only says what the profile and answer bank support.** It never invents experience;
   an unanswerable question goes to the user (plan §3).
8. **A person always clicks the final Submit.** No auto-submit setting, now or later (plan §10).
9. **Never scrape LinkedIn or Indeed, never automate Easy Apply, never bypass a CAPTCHA** (plan §4, §6).

## Testing

`./test.sh` — ruff, a fresh `apply_test` database migrated to head, then pytest in parallel, all
inside the dev container. Real Postgres, no SQL mocks. Details: `docs/testing.md`.

## Versioning, git, release

- `0.MINOR.PATCH`; one MINOR release per milestone (`VERSIONING.md`, plan §9).
- Issue (Given/When/Then criteria) → branch `<issue#>-slug` → PR with `Closes #N` → squash-merge.
  No direct pushes to `main`. Commit subjects imperative and capitalised; the body says why.
- `CHANGELOG.md` `## [Unreleased]` must be updated by any PR touching `app/` (CI-enforced).
- Every `Scenario:` in a closed issue must be claimed by a `criterion` test or a `Verified by hand:`
  line in the PR body (CI-enforced, `scripts/check_criteria.py`).
- Release = publish a GitHub Release `vX.Y.Z`; `release.yml` builds, smoke-tests and scans the
  image, then deploys after `production` approval. `rollback.yml` redeploys an older version.

## Current status

See `docs/status.md`.

## Maintainer notes (local only)

Host names, the deploy user and vault paths go in the gitignored `CLAUDE.local.md`.
