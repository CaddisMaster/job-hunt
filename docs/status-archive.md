# Status archive

Older sessions from `docs/status.md`, unchanged, newest first.

**2026-11-19 (afternoon).**
- **0.3 started:** `job_runs` (#50), the worker (#51, closes #41), `employers` (#52) and the
  registry at /admin/employers (#53, closes #43). The 0.3 PRs other than #50 were held, green,
  until v0.2.0 shipped.
- **v0.2.0 released and deployed** (#55, run 44591442318). The backup chain was run early
  instead of waiting for the timers: the droplet dump started from the Mac, the VM pull, then
  the Mac's restore drill. That closed #5 and #21 (#54).
- **Learned:**
  - Releases are cut from `main`, so the next milestone mustn't merge before the current one is
    released. #50 slipped into 0.2.0; it's an empty table, so it's harmless.
  - Merging `main` into a held branch after `[Unreleased]` became `[0.2.0]` put the branch's
    changelog entry in the released section, with no conflict.
  - `gh pr merge --delete-branch` on a stack's base **closes** the PR above it. Retarget first.
  - Before a tenant's first nightly timer, `backup-export` hands over the pre-deploy dump, which
    has no tables.
  - A dump restores into a fresh Postgres only once the roles `apply` and `apply_app` exist
    (RUNBOOK §6).
  - `gh release create --target` takes a branch or a full SHA, not a short one.
  - GeoNames is blocked from the Mac too, not just the VM.

**2026-11-19 (morning).**
- **v0.1.0:** landed #10 after its LXD test caught a real ordering bug (the role checked `.env`
  before the deploy key and had already changed the host). Made the repo public, because a private
  repo on GitHub Free can't have a required reviewer on `production`. A Claude session on Sean's
  Mac, briefed over Remote Control, did every droplet and secret step with Sean's approval. This
  VM session never handled a secret. Deployed v0.1.0.
- **0.2, all merged:** the spend ledger with per-user and key caps (#22), resume upload stored in
  Postgres (#23), the profile built by one Opus 5.5 call, grounded in the resume and confirmed by
  its owner (#24), the answer bank (#25), and the truthfulness rule as a deterministic check with
  an eval set (#26). Each migration shipped alone, labelled `skip-changelog`.
- **Infra:** droplet-infra gained `backup-export job-hunt` (#11) and an Nginx version floor that
  applied USN-9159-1 to the droplet (#13). The VM now encrypts Job Hunt's pulled dumps with `age`
  in memory (#21). Moved to Python 3.15 (#40).
- **Learned:**
  - GitHub reads "Closes: #N" anywhere in a PR body, even after "not".
  - Dependabot's pip scan of `/` includes subdirectories.
  - `request.form()` holds Starlette's `UploadFile`, not FastAPI's subclass.
  - The VM can reach census.gov but not geonames.org from Sean's work network.
  - unattended-upgrades does cover `noble-security` on the droplet.

Decisions are in `docs/plan.md` §10 (answered 2026-11-18).

**2026-11-18.** Planned the app, answered the §10 decisions, created the repo and filed #1–#5.
Built all five: #1–#4 merged (#6–#9), #10 open. Stacked PRs were landed by merging `main` into
each next branch (force-push is blocked), keeping the branch's side of each add/add conflict and
checking the tree didn't change. Found that Orbit Ops's `infra/requirements-infra.txt` pins
`ansible-core==2.22.0` (its Dependabot #419), which needs Python 3.13 — the VM has 3.12, and
droplet-infra pins 2.21.5. Job Hunt pins 2.21.5; Orbit Ops's own `infra/test.sh` is broken until
it does too.
