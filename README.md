# Job Hunt (working name)

A daily control center for job applications: finds Pittsburgh-area jobs that match a user's
resume, drafts tailored materials overnight, and fills out the applications the user approves
each morning. Multi-user, invite only, for Sean and family and friends.

**Status: planning only, nothing built yet.** The whole plan is in [`docs/plan.md`](docs/plan.md).
Its §10 decisions were answered on 2026-11-18 (address: `apply.seandesmet.com`), so the next step is filing the 0.1 issues.

Planned stack and workflow: the same as Orbit Ops (FastAPI, Postgres, Docker Compose, ghcr,
GitHub Release deploys, a tenant on the shared droplet via `droplet-infra`).
