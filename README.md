# Job Hunt (working name)

Sean's job application helper. He pastes links to jobs he found; Job Hunt saves each posting,
tailors his resume to it, and fills in the application with Playwright. He always clicks the final
Submit himself.

**Status:** v0.2.0 is live; v0.3.0 (Saved jobs) is on `main`. Where things stand:
[`docs/status.md`](docs/status.md). The plan, and the 2026-11-20 change of shape: [`docs/plan.md`](docs/plan.md). Running it locally: `CONTRIBUTING.md` §1.

Planned stack and workflow: the same as Orbit Ops (FastAPI, Postgres, Docker Compose, ghcr,
GitHub Release deploys, a tenant on the shared droplet via `droplet-infra`).
