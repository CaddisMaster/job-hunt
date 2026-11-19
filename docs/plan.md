# Job Hunt: the plan

*Drafted 2026-11-18. Status: **planning only, nothing built.** The open decisions in §10 were
answered on 2026-11-18, so the next step is filing the 0.1 issues.*

A control center for job applications. Each night it finds jobs in the Pittsburgh area that match
the user's resume and drafts the materials. Each morning the user opens one page, approves the
applications they want, and a worker on the droplet fills them out. It is for Sean first, then
invited family and friends.

## 1. Where it lives and what it's built on

**Address:** `apply.seandesmet.com` (decided, §10), a subdomain like `budget.` and `learn.`.
`seandesmet.com` is a single static `index.html`, so the landing page only gets a link.

**Stack:** copy **Orbit Ops**, not Budget Buddy. Orbit Ops is the newer of the two and already
includes Budget Buddy's lessons.

| Layer | Choice (same as Orbit Ops) |
|---|---|
| Web | FastAPI, SQLAlchemy 2.0, Alembic, psycopg 3, argon2 logins, CSRF, strict CSP with nonces |
| UI | Jinja2 + vendored htmx, plain CSS, no build step |
| Data | Postgres 16 in its own container, never published to the host |
| AI | One `ai.py` seam for every model call, plus Orbit Ops's spend ledger and monthly budget check |
| Ops | Docker Compose, ghcr, GitHub Release → `release.yml` deploys after `production` approval, an `infra/` Ansible role on droplet-infra's pinned host role, LXD tests, `./test.sh` |
| Process | Issue (Given/When/Then) → branch `<issue#>-slug` → PR `Closes #N` → squash-merge, `CHANGELOG.md`, `0.x` versions, a `CLAUDE.md` with the "Before you… / Read" table |

**New droplet tenant** (a change in `CaddisMaster/droplet-infra` too): `/opt/apply`, its Nginx
site, its certificate, and its database dumps added to `backup-export`.

**Compose services:**

- **`web`:** the control center.
- **`worker`:** the scheduler, like Budget Buddy's `worker` service (`flask run-scheduler`). Runs
  the nightly search, scoring and drafting.
- **`applier`:** Python Playwright with headless Chromium. Takes approved applications from a
  queue table in Postgres, **one at a time**.
- **`db`.**

**The agent:** a plain tool-use loop on the `anthropic` SDK already pinned in both apps
(`anthropic==2.8.0`), inside `ai.py`. Not the Claude Agent SDK, which would put Node and the
Claude Code CLI into the image when no other app needs them.

⚠️ **Droplet size is the biggest constraint.** `droplet-infra/infra/terraform/main.tf` says
`s-1vcpu-2gb`, already shared by Budget Buddy and Orbit Ops. Headless Chromium uses roughly
300–500 MB.

- **Decided (§10): resize the droplet to 4 GB before 0.6 ships.** The resize goes in
  `droplet-infra`'s Terraform as its own change, done and checked before the applier is deployed.
- Even at 4 GB, keep the applier at one browser at a time with a hard container memory limit,
  and measure.

## 2. Multiple users (family and friends)

- **Invite only.** The admin creates accounts (Orbit Ops already has this, #282). No public
  sign-up.
- **Every private table carries `user_id`** and every query is scoped to it, the rule both apps
  already hold.
- **Rate limiting in the database from day one.** Orbit Ops's in-process limiters only work for
  one user (its non-negotiable #1 says so).
- **Per-user monthly AI budget.** Everyone spends against Sean's API key (decided, §10), so
  the spend ledger gets a cap for each user.
- **Shared vs private data:**
  - Shared: the employer registry and job postings. One nightly crawl serves everyone.
  - Private: resumes, answers, matches and applications.
- **Tell people what the admin can see.** The admin can see their resumes and answers. Say so on
  the invite, and keep admin views minimal.

## 3. Profile: everything comes from the resume

- **Upload (PDF or DOCX).** Claude turns it into a structured profile: titles, skills, years,
  seniority, education, certifications. The user checks and edits it once.
- **Search targets come from the profile, not keywords the user types:**
  - Target titles plus nearby ones (e.g. "DevOps Engineer" → SRE, Platform Engineer, Cloud
    Engineer).
  - Seniority band.
  - Must-have and nice-to-have skills.
- **Answer bank:** work authorization, sponsorship, salary expectation, start date, contact
  details, links, EEO answers (default "decline to answer"). New questions are added once the
  user approves an answer.
- **Truthfulness rule:** the agent may only say what the profile and answer bank support. If the
  honest answer is "I don't have this," it says that or flags the question for the user. It
  never invents experience.

## 4. Finding jobs in the Pittsburgh area

**"Greater Pittsburgh" needs a fixed definition.** Proposed: the Pittsburgh metro area, the
counties of Allegheny, Armstrong, Beaver, Butler, Fayette, Washington and Westmoreland. Stored as
a geocoded radius (about 40–50 miles) plus the county list. Remote is a per-user toggle, **off by
default** (decided, §10).

**Sources, most reliable first:**

1. **Pittsburgh employer registry (shared).** Many employers' careers pages expose a public API
   through their hiring system (Greenhouse, Lever, Ashby, SmartRecruiters, Workday's JSON
   endpoints). The first user is Sean alone (§10), so seed it first with employers hiring
   DevOps/SRE/platform/cloud roles: Duolingo, Aurora, Gecko Robotics, the tech teams at PNC, BNY,
   Highmark and UPMC, CMU and Pitt IT, and so on. Widen it when other users join. Most reliable because the data is
   clean and structured.
2. **Aggregator APIs:** Adzuna (free tier, radius search) and USAJobs (federal, free). These catch
   employers missing from the registry, and a new one can be suggested for the registry.
3. **Never:** scraping LinkedIn or Indeed, or automating Easy Apply. Both ban it and do ban
   accounts, which is an especially bad risk to put family members' accounts at.

**Nightly pipeline (`worker`):**

- Fetch every source and normalize the postings.
- Remove duplicates (same company, same title, about the same location).
- Drop anything outside the area.
- For each user, a cheap model screens out obvious mismatches, then a stronger model scores the
  rest 0–100 with strengths, gaps and dealbreakers.
- Only postings above the user's threshold go on.

## 5. The daily loop

**Overnight,** for each strong match, draft:

- A tailored resume. It reorders and rewords existing material, never adds to it, and shows a
  diff against the base resume.
- Answers to the form's questions.
- A cover letter, only if the posting asks for one.

**Morning:** reuse Budget Buddy's Resend mailer and Web Push pattern for "6 new matches ready."

**The queue page:** one card per job, showing the score and reasons, a link to the posting, the
resume diff and the drafted answers. Buttons: **Approve / Edit / Skip / Not interested in this
company.** Optional daily cap per user.

**Feedback:** skips and "not interested" feed back into scoring.

## 6. The applier: approval → submitted

**Fixed scripts for known forms.** Greenhouse, Lever and Ashby forms always have the same
structure, so a deterministic Playwright script fills the standard fields. The model only matches
unusual questions to the answer bank. Cheaper, faster and more predictable than an agent working
everything out.

**General agent for everything else** (later, 0.8+). Reads the page's accessibility tree and
calls tools like `fill`, `select`, `upload`, `ask_user`, `stop`, under a cap on steps and spend.

**Parking jobs it can't finish.** Any of these sends the job to a **Needs you** list instead of
guessing:

- A question with no answer in the bank
- A CAPTCHA (never bypassed)
- A form that requires creating an account
- A file it can't handle
- Anything unexpected

Once the user answers, the run resumes.

**One submission mode, always:** fill everything, take a full-page screenshot, and wait for the
user's second click. **The applier never submits without a person clicking the final Submit**
(decided, §10). There is no auto-submit setting, now or later.

**Workday, iCIMS, Taleo** need an account per employer, so they're out of scope for v1. Those
jobs show as **Apply yourself** cards with the drafted answers ready to copy.

**Safety rules:**

- Never apply to the same posting twice.
- Limit applications per company per month.
- Keep a screenshot and the confirmation text for every submission.

## 7. Tracking after applying

- **Pipeline board:** Queued → Submitted → Response → Interview → Offer / Rejected / Ghosted.
  Manual updates in v1.
- **Later, status from email.** Gmail OAuth would need Google's security review for every user,
  so instead each user sets a Gmail filter forwarding to `apply+<token>@`, received through
  Resend inbound. Claude sorts the replies.
- **Reminders:** a follow-up nudge after N days.
- **Stats:** response rate by source, score band, resume version and posting age.

## 8. Data model (first draft)

| Shared | Private (has `user_id`) |
|---|---|
| `employers` (name, hiring system, board token, enabled) | `profiles`, `resumes` (base + tailored versions) |
| `postings` (source, URL, title, location, geo point, description, first/last seen, closed) | `answer_bank` |
| | `matches` (posting, score, reasons, status) |
| | `applications` (match, state, screenshots, confirmation) |
| | `apply_runs` (step log, spend) |
| | `ai_spend` |
| Bookkeeping: `job_runs`, `crawl_runs` | |

Resumes and screenshots go on a Docker volume and into backups. ⚠️ The dumps then hold other
people's personal data, so check where `backup-export` sends them.

## 9. Milestones

| Version | What ships |
|---|---|
| **0.1** | Skeleton copied from Orbit Ops: auth, admin creating users, droplet tenant, release pipeline, `/healthz` |
| **0.2** | Resume upload → structured profile → answer bank |
| **0.3** | Discovery: employer registry, Greenhouse/Lever/Ashby, Adzuna, location filter, removing duplicates |
| **0.4** | Scoring and the morning queue, with email and push. **Useful on its own:** apply by hand from good matches |
| **0.5** | Tailored resumes and drafted answers, with the diff view |
| **0.6** | Applier for Greenhouse/Lever/Ashby (review, then a human clicks Submit), plus Needs you. **Resize the droplet to 4 GB and measure memory before this ships** |
| **0.7** | Pipeline board, reminders, stats |
| **0.8+** | Email forwarding for status, general agent for unknown sites, Workday |

0.4 comes first because it is useful within weeks, and the riskiest piece (browser automation on
a 2 GB droplet) comes after the matching has proven worth it.

## 10. Decisions (answered 2026-11-18)

| # | Question | Decision |
|---|---|---|
| 1 | Name and subdomain | **`apply.seandesmet.com`**. Droplet tenant path is `/opt/apply`. `job-hunt` stays as the folder's working name only. |
| 2 | Who pays for AI | **Sean's API key**, with a monthly cap per user enforced by the spend ledger. No bring-your-own-key. |
| 3 | Droplet | **Resize to 4 GB before 0.6.** One host and one host role. The applier still runs one browser at a time with a memory limit. |
| 4 | Remote jobs | **Per-user toggle, off by default.** Pittsburgh metro only unless the user turns remote on. |
| 5 | Auto-submit | **Never.** A person always clicks the final Submit after seeing the screenshot. Not a setting, and not on the roadmap. |
| 6 | First users | **Sean only** for now. Seed the registry for DevOps/SRE/platform/cloud roles and widen it when others are invited. Multi-user scoping (§2) still gets built from 0.1. |
