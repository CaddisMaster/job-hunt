# Status archive

Older sessions from `docs/status.md`, unchanged, newest first.

**2026-11-18.** Planned the app, answered the §10 decisions, created the repo and filed #1–#5.
Built all five: #1–#4 merged (#6–#9), #10 open. Stacked PRs were landed by merging `main` into
each next branch (force-push is blocked), keeping the branch's side of each add/add conflict and
checking the tree didn't change. Found that Orbit Ops's `infra/requirements-infra.txt` pins
`ansible-core==2.22.0` (its Dependabot #419), which needs Python 3.13 — the VM has 3.12, and
droplet-infra pins 2.21.5. Job Hunt pins 2.21.5; Orbit Ops's own `infra/test.sh` is broken until
it does too.
