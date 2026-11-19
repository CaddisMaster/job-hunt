# Status

*Reconcile this against `git log` and `gh issue list` at the start of a session.*

**Milestone v0.1.0 — Skeleton** (plan §9). The code for all five issues is written, as stacked
PRs (#6 → #2's → #3's → #4's → #5's), each passing `./test.sh`. Nothing is deployed.

Before the first release, by hand (RUNBOOK §2, §4):
- Job Hunt's own deploy key, the `production` environment and its secrets, branch protection.
- DNS for `apply`, the certificate, `.env`, the droplet's ghcr login (the package is private).
- droplet-infra: a `job-hunt` command in `backup-export`, so the VM pulls the dumps.
- Where the VM keeps pulled dumps, and whether they're encrypted there (#5) — before inviting anyone.
- `infra/test.sh` hasn't been run (it loads Sean's Mac for ~5 minutes; ask first).

Decisions are in `docs/plan.md` §10 (answered 2026-11-18).
