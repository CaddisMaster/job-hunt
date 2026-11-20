# Status

*Reconcile this against `git log` and `gh issue list` at the start of a session.*

**v0.2.0 is live** at apply.seandesmet.com (deployed 2026-11-19). On `main` for v0.3.0, not yet
released: the worker (#41), the employer registry (#43) with its Pittsburgh starter list, the
area check (#42) and the nightly crawl from Greenhouse, Lever and Ashby (#44). Open in 0.3: #45,
#46, #47, and #59 (parked).

## Pick up here

1. **#45: Adzuna and USAJobs.** Next to build; no migration needed (`postings.source` already
   allows both). Blank keys = that source skipped. This is how UPMC, PNC, Highmark, CMU and
   Covestro get in (see 3). Daily budget: `RateLimiter(name, budget, 86400)` from
   `app/rate_limit.py`. Adzuna's `redirect_url` is Adzuna's own page, so the LinkedIn/Indeed drop
   can only check the URL we're given. Unverified in the docs (check once Sean has keys): Adzuna's
   US path (`/v1/api/jobs/us/search/<page>`), its radius parameter and unit, its free-tier limits,
   and USAJobs' radius unit. The crawl's `Skip` when the registry is empty must change: skip only
   if no source at all can run.
2. **The API key (Mac), still unconfirmed:** check `ANTHROPIC_API_KEY` reached the droplet's
   `.env` and `job-hunt-env-prod`, then Sean builds his profile from his real resume.
3. **#59 Workday is parked.** The migration shipped (#62); the fetcher is on the **local,
   unpushed** branch `59-workday` (`6e14af9`). Cloudflare answered the app's requests with a bot
   challenge (`429`, `cf-mitigated: challenge`) after about 15 requests in a few minutes, so it's
   off the table unless Adzuna's coverage of those employers is thin. UPMC's feed refuses anything
   but a browser on UPMC's page. Neither is to be got around (non-negotiable 9, and Job Hunt always
   identifies itself).
4. **Then #46 (duplicates) and #47 (the Jobs page).** #63 (paste a link, Job Hunt does the rest)
   is a fallback idea, only if discovery stays thin after 0.3 runs for a couple of weeks.
5. **Area follow-ups seen in the first real crawl's unknown list:** "Mexico City" (no country
   named), "Onsite- Salem, OR" (a dash without a space isn't split), "United States" alone.
6. **When v0.3.0 deploys:** run `alembic upgrade head` (the release does it), press "Add the
   starter list" at /admin/employers, and watch the worker's memory during the 02:30 crawl
   (`docker stats`, 192 MB cap; 62 MB in dev after a crawl run as a separate process).
7. **droplet-infra's Dependabot:** #5 and #6 there bump `ansible-core` to 2.22, which breaks on
   the VM's Python 3.12. Close them and exclude `infra/` the way Job Hunt's #38 did.

## Sessions

**2026-11-19 (evening).**
- **Merged:** #48 (closes #42: Greater Pittsburgh, with GeoNames' post-office names), #57 + #58
  (closes #44: the `postings` table, then the crawl), #60 (same-named townships), #61 (the
  starter list), #62 (Workday's migration). Filed #59 (Workday, parked) and #63 (the fallback).
- **Verified in dev:** the starter list's 25 employers crawled for real: 4,151 jobs, 365 shown,
  119 unknown, no errors.
- **Learned:**
  - Run new parsers against live boards before merging. That found Ashby's `isRemote` being
    true for hybrid jobs, foreign cities and "D.C." coming out `unknown`, and Lancaster/Fairfield
    (townships here, towns out east) counting as local.
  - Seeding the registry from target titles (plan §4) left the search idle; a probed starter
    list replaced it. Most of the region's biggest employers aren't on Greenhouse, Lever or
    Ashby.
  - Cloudflare in front of Workday challenges bursts of automated requests; UPMC's job feed
    checks where requests come from. Auto mode also blocks calling a vendor's undocumented
    endpoint until Sean approves it.
  - `./test.sh` migrates only `apply_test`; the dev database needs `alembic upgrade head` after
    pulling migrations.
  - `gh pr merge --delete-branch` also deletes the local branch, so `git rebase --onto` needs
    the old commit, not the branch name.
  - GeoNames was reachable from the VM in the evening (blocked in the morning on the work network).
- **From now on:** no Claude attribution in commits or PR bodies (Sean, 2026-11-19).

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
