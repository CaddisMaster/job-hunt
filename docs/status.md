# Status

*Reconcile this against `git log` and `gh issue list` at the start of a session.*

**v0.2.0 is live** at apply.seandesmet.com (deployed 2026-11-19). **Everything below is on `main`,
not released**: production is still at migration 0008, and v0.3.0 goes straight to 0013.

**How it works now** (plan §10 decisions 7–11, and the box at the top of the plan): Job Hunt is
Sean's alone. He pastes job links into **Saved jobs**. Pressing **Apply** on a job (or **Build my
profile**) queues a run. The runner on the VM (`jobhunt-runner.service`) starts Claude Code
headless with only the `jobhunt` and `playwright` tools. It tailors his resume, which the web app
checks against the resume and confirmed profile before making the PDF, then fills the application
in a browser on the VM's virtual screen. Sean watches that browser through Screen Sharing (VNC over
SSH). Questions, the browser hand-off and the final **Submit application** happen on the job's page.
The web app makes no Claude calls. No crawling, no ranking. Setup and use: `docs/apply.md`.

## Pick up here

1. **Release v0.3.0** (the release PR moves `[Unreleased]` to `[0.3.0]`). It needs Sean's
   `production` approval. Production goes from migration 0008 straight to 0013. Then:
   - Sean makes a token on **production's** Profile page and replaces `~/.config/jobhunt/token`;
   - `tools/apply-services.sh install` (no URL means production) points the runner at it;
   - `claude mcp` for `jobhunt` is re-added without `JOBHUNT_URL`.
2. **After the release, the Mac session:** take `ANTHROPIC_API_KEY` and the two
   `AI_MONTHLY_BUDGET_*` keys out of the droplet's `.env` and `job-hunt-env-prod`. They're unused
   since #80, and harmless until then (`extra="ignore"`).
3. **Still untested end to end:** filling a real form and the Submit step. The one real run (UPMC,
   Taleo) stopped at the site's sign-in page. A Lever or Ashby job has no account wall.
4. **Open issues waiting on Sean's choice before criteria:** #94 (tailored resume in his own
   format: is there a .docx?), #92 (the browser inside the web app: noVNC, a view-only picture, or
   both), #93 (a calmer saved-job page: mock first).
5. **Small things:**
   - pasted Greenhouse links ask for the text, because their pages carry no job data;
   - drop the `ai_spend` table in a later release than v0.3.0 (non-negotiable 5).
6. **Dev databases from before 2026-11-20 midday:** 0009–0011 were deleted and renumbered (#72).
   One at 0010–0012 from then must go back to 0008 with the old files (`git checkout f7c0ea6`)
   before upgrading.

## Sessions

**2026-11-23.**
- **The first web-driven run held up** (run 1, 2026-11-20, a UPMC job on Taleo): `claude -p`
  started under the runner with no first-run prompts, the MCP tools worked, two questions were
  answered on the page, the tailored PDF was fetched, and the browser was handed to Sean at
  Taleo's sign-in. It stopped there.
- **Merged:** #95 (Dependabot: fastapi, pydantic, SQLAlchemy, ruff) and #97 (Rebuild my profile,
  closing #96). droplet-infra #16 holds `ansible-core` below 2.22 and stops the root scan bumping
  `infra/`; its #5 and #6 were closed.
- **Cleaned up:** 16 merged local branches and the dead `59-workday`.
- **Learned:** the VM's battery drain on the Mac is mostly k8s-lab's control plane (always busy)
  and 12 containers across five projects; the guest itself sits about 97% idle.


**2026-11-20.**
- **The aggregators were built, then closed unmerged** (#65, #45): Adzuna's free tier is 25 calls
  a day and 500 a month, and its links go through its own page.
- **Saved jobs** (#69, #70, closing #63): paste a link, and the posting is read from the page's
  JSON-LD or microdata, else Sean pastes the text. The server's GET is guarded against SSRF.
- **Discovery removed before release** (#71, #72): the crawl, registry, area check and board APIs
  went, and their unreleased migrations were deleted.
- **The pivot**, recorded in plan §10 decisions 7–11 (#68): Sean alone; jobs only by pasting;
  Claude Code does the AI work; the web app checks, renders and records.
- **Built, in order:**
  - the bridge: tokens and a JSON API (#77, #78, closing #74);
  - tailored resumes, checked and rendered as a PDF with ReportLab (#75, #79, closing #73);
  - the profile build moved into Claude Code, and the app's own Claude calls, budgets and
    `anthropic` removed (#80);
  - applications, site notes and the MCP server `tools/jobhunt_mcp.py` (#81, #82, closing #76);
  - the `/apply` and `/profile` skills (#83, closing #66);
  - applying on the VM, with the browser shown on the Mac (#84), Chromium's AppArmor profile
    (#86) and the screen as services (#87);
  - web-driven runs: a button, a live Run panel, answers on the page, and the runner (#89, #90,
    closing #88).
- **Also:** the sign-in page's "Ask Sean" line went (#85, closing #67), and a minute-boundary
  flake in the login-limit test was fixed in #78.
- **Verified for real:**
  - Saved jobs against live Greenhouse, Lever, Ashby, Workday and SmartRecruiters links;
  - Chromium, Xvfb and VNC on the VM, viewed from the Mac;
  - `/profile` from an interactive Claude Code session against dev.
- **Learned:**
  - Greenhouse job pages carry no structured job data, and SmartRecruiters uses microdata, not
    JSON-LD.
  - `@playwright/mcp` 0.0.84 has no `chromium` choice, and without `--user-data-dir` sign-ins
    don't persist.
  - Ubuntu 24.04 needs an AppArmor `userns` profile for Chromium's sandbox.
  - `gh pr merge --delete-branch` removes the local branch, so rebase stacks from the old SHA.
  - Fixed rate-limit windows are clock-aligned, so burst tests must start early in a minute
    (`fresh_rate_window`).
  - Denying Claude Code reads of `~/.config/jobhunt/` also blocks harmless checks there, which is
    intended.
- **Sean's calls:** Playwright as an honest browser only (no stealth, CAPTCHAs are his); PDFs for
  tailored resumes; Submit as a button in Job Hunt after he's seen the filled form.

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
