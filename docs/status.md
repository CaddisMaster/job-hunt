# Status

*Reconcile this against `git log` and `gh issue list` at the start of a session.*

**Milestone v0.1.0 — Skeleton** (plan §9). Nothing is deployed.

## Pick up here

1. **Finish PR #10 (part of #5).** Its criteria check fails only on "The infra role passes its LXD
   test". After a VM reboot, re-add the two iptables rules (`infra/README.md`), run
   `infra/test.sh` (~5 minutes on the Mac), then add to #10's body
   `Verified by hand: #5 "The infra role passes its LXD test" — <the PASS line>`, re-run the check,
   and squash-merge. Check the CI run on `main` after.
2. **#13:** port `/wrap`, the changelog Stop hook and the allowlist from Orbit Ops.
3. **#5's hand steps** before the first release (RUNBOOK §2, §4): Job Hunt's deploy key, the
   `production` environment and secrets, branch protection, DNS for `apply`, `certbot certonly`,
   `.env`, the droplet's ghcr login, droplet-infra's `backup-export` command, and where the VM keeps
   pulled dumps (encrypted?) — before inviting anyone. Then v0.1.0.

## Sessions

**2026-11-18.** Planned the app, answered the §10 decisions, created the repo and filed #1–#5.
Built all five: #1–#4 merged (#6–#9), #10 open. Stacked PRs were landed by merging `main` into
each next branch (force-push is blocked), keeping the branch's side of each add/add conflict and
checking the tree didn't change. Found that Orbit Ops's `infra/requirements-infra.txt` pins
`ansible-core==2.22.0` (its Dependabot #419), which needs Python 3.13 — the VM has 3.12, and
droplet-infra pins 2.21.5. Job Hunt pins 2.21.5; Orbit Ops's own `infra/test.sh` is broken until
it does too.

Decisions are in `docs/plan.md` §10 (answered 2026-11-18).
