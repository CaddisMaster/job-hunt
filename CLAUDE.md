# Job Hunt

A daily control center for job applications at **apply.seandesmet.com**. Each night it finds
Pittsburgh-area jobs that match the user's resume and drafts the materials; each morning the user
approves the ones they want, and a worker fills out the forms. A person always clicks the final
Submit. Multi-user, invite only: Sean first, then family and friends. FastAPI + Postgres in Docker,
the third tenant on the droplet shared with Budget Buddy and Orbit Ops. Built by copying Orbit Ops.

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

- **Backend:** Python 3.14, FastAPI (sync routes), SQLAlchemy 2.0 ORM, psycopg 3, Alembic,
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
app/admin.py         the admin panel's queries — accounts only, never anyone's private data
app/templating.py    the shared Jinja env (app_version, static_url, pop_flash globals)
app/static_files.py  static_url (?v= per release) and Cache-Control on /static
app/routers/         main.py (dashboard, /healthz), auth.py (login/logout), invite.py (/invite/<token>), admin.py (/admin)
alembic/versions/    migrations (0001 the apply_app role, 0002 users + rate_limits, 0003 invites + disabled_at)
infra/               the droplet as code: ansible/ (the app role on droplet-infra's pinned host role), systemd/ (backup units), test.sh (LXD)
scripts/             create_user.py (bootstrap the first admin), check_criteria.py (CI), install_compose.sh + backup_db.sh (deploy)
```

## Non-negotiables

1. **Every query that reads a user's private data is scoped to the current user.** Resumes, the
   answer bank, matches and applications all carry `user_id`. Shared tables (employers, postings)
   are the only exception, plus the admin module, which names its target user explicitly.
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
