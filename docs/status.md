# Status

*Reconcile this against `git log` and `gh issue list` at the start of a session.*

**v0.1.0 is live** at apply.seandesmet.com (deployed 2026-11-19). **v0.2.0's code is all merged
but not released.** v0.3.0 (Discovery) is filed as #41–#47.

## Pick up here

1. **Check the first real nightly backup pull** (the VM, about 08:00 New York time, from
   2026-11-20). `backups/logs/droplet-backup.log` should show
   `OK job-hunt -> job-hunt_<date>.sql.gz.age`. Then:
   - **#5:** a PR with `Verified by hand:` lines for its remaining scenarios (HTTPS at the address,
     the other tenants, backed up off the droplet) closes it and the v0.1.0 milestone. Write
     `Part of` / `Closes` carefully: "not Closes: #5" in #10's body closed it by accident once.
   - **#21:** the restore drill. Message the Mac session with the `.age` file to fetch from the VM.
     Sean unlocks Bitwarden (`job-hunt-backup-age-identity`), and the Mac decrypts the dump,
     restores it into a scratch Postgres and checks the `users` rows. The identity never goes to
     the VM. That closes #21, the last v0.2.0 issue.
2. **Release v0.2.0:** a changelog PR (`[Unreleased]` → `[0.2.0]`), publish the Release, Sean
   approves. Then, from the Mac: `ANTHROPIC_API_KEY` into the droplet's `.env` and Bitwarden. Sean
   builds his profile from his real resume; that's the first real Claude call, never yet made
   against the API.
3. **Finish #42 (draft PR #48):** fetch GeoNames' `US.zip` (download.geonames.org) from a network
   that allows it, since Sean's work network blocks it. Put it in the VM's scratchpad, rebuild
   `app/data/places.csv` with the third argument, add the Wexford and Warrendale cases, finish the
   attribution in `app/data/README.md` and the changelog line, then mark it ready.
4. **Then 0.3 in order:** #41 (worker) and #43 (employers), then #44, #45, #46 and #47.
5. **droplet-infra's Dependabot:** #5 and #6 there bump `ansible-core` to 2.22, which breaks on the
   VM's Python 3.12. Close them and exclude `infra/` the way Job Hunt's #38 did. #1 and #8 there
   are routine.

## Sessions

**2026-11-19.**
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

**2026-11-18.** Planned the app, answered the §10 decisions, created the repo and filed #1–#5.
Built all five: #1–#4 merged (#6–#9), #10 open. Stacked PRs were landed by merging `main` into
each next branch (force-push is blocked), keeping the branch's side of each add/add conflict and
checking the tree didn't change. Found that Orbit Ops's `infra/requirements-infra.txt` pins
`ansible-core==2.22.0` (its Dependabot #419), which needs Python 3.13 — the VM has 3.12, and
droplet-infra pins 2.21.5. Job Hunt pins 2.21.5; Orbit Ops's own `infra/test.sh` is broken until
it does too.

Decisions are in `docs/plan.md` §10 (answered 2026-11-18).
