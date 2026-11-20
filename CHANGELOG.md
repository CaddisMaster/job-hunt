# Changelog

All notable changes to Job Hunt. The format follows [Keep a Changelog](https://keepachangelog.com);
versions follow `VERSIONING.md`. Write entries for someone reading them in a year: what changed
and why it matters, not what the diff says.

## [Unreleased]

Job Hunt is now Sean's alone, and the only way jobs get in is the link he pastes: no crawling,
no ranking (plan §10, decisions 7, 8 and 10). The discovery built earlier in 0.3 (an employer
registry, a nightly crawl of Greenhouse, Lever and Ashby boards, and a Greater Pittsburgh filter)
never shipped and has been removed.

### Added
- Tailored resumes (#73). In a Claude Code session, Claude chooses which lines of Sean's resume
  lead for one saved job and sends them to Job Hunt, which checks every line before making a PDF.
  Roles, titles and dates always come from his confirmed profile. A bullet must be in his resume
  under that same job, so it can't move to another one. Skills and degrees must be confirmed ones.
  Anything that fails is left off and listed with the reason. The PDF is plain, one column and
  real text, which applicant tracking systems can read. It's stored with the job, and it's only
  used to apply once he approves it, on the job's page or in the session. A new version needs
  approving again.
- Access tokens and a small JSON API (#74), so Claude Code on Sean's Mac can work with Job Hunt:
  list his saved jobs, fetch everything for one (the posting, his resume's text, his confirmed
  profile and his answer bank), and add an answer he gave during an application to the answer
  bank, checked the way the answers page checks it. Tokens are made on the Profile page, shown
  once, stored only as a hash, and can be revoked. The API takes only a token, never the login
  cookie, and wrong tokens are rate-limited. This is the bridge that tailoring and applying from
  Claude Code are built on.
- Saved jobs (#63): paste a link to a job you found anywhere, and Job Hunt reads the posting into
  your list at /saved. The page is opened once and the job details it embeds for search engines
  are read (both schema.org forms, JSON-LD and microdata, so Lever, Ashby and SmartRecruiters
  pages work). When that fails (a page built by script, a login, a bot check, and Greenhouse's
  job pages, which carry no job details), the job is saved with the reason and you paste the
  posting's text. Job Hunt never opens LinkedIn or Indeed links, stops at a bot check rather than
  getting round it, and only opens pages on the public internet, checking every redirect. Saved
  jobs are yours alone.
- A `worker` service (#41) that runs scheduled jobs outside the web app: it's where tailoring
  and other background work will run. Each job runs once a day at its time in New York time; if
  the server was down for a few nights, it runs once when it's back, not once per missed night,
  and two workers can never run the same job at once. A job that fails is recorded and logged, and
  the others still run. The admin page lists each job with its last run and how it went. The first
  job is housekeeping, which keeps 90 days of that history.

## [0.2.0] - 2026-11-19

The profile milestone: upload your resume, get a profile built from it that you check and
confirm, and fill in the answers applications ask for. The first Claude calls, within a monthly
budget.

### Added
- The truthfulness rule in code (#26). Anything Job Hunt will ever write for you, starting with the
  drafted answers in 0.5, is first checked against the profile you confirmed and your answer bank:
  a claim is made only if they support it, dropped if they don't, and brought to you as a question
  if only you can answer it. The check is exact and doesn't use a model: a related skill isn't the
  skill, years are never rounded up, and "Prefer not to say" is respected. Building a profile
  uses the same check to drop anything not actually in your resume.
- An answer bank (#25), linked from the Profile page: the questions applications ask that a resume
  doesn't answer, such as work authorization, sponsorship, salary, start date, relocation, contact
  details and links. Anything left blank stays blank and means "ask me": it's never guessed or
  filled from your account. The voluntary EEO questions default to "Decline to answer", and remote
  jobs are off (Pittsburgh area only) until you turn them on. A form with a mistake in it saves
  nothing and says what to fix.
- A profile built from your resume (#24). One Claude call reads your base resume into roles,
  skills, education, certifications, years, seniority and the job titles to search for. It is told
  to extract, never infer, and to quote the resume line behind every title and skill; anything
  whose quote isn't actually in your resume is dropped before you see it. You then check each item
  beside its quote, fix or remove what's wrong, and confirm. Nothing later uses an unconfirmed
  profile. Search targets stay blank until this is done: no role is assumed for anyone. Building
  one counts toward your monthly AI budget, and rebuilding from a newer resume asks first.
- Resume upload (#23), on a new Profile page. A PDF or Word (.docx) file up to 5 MB becomes your base
  resume, and older ones are kept. The file's type is decided by what's inside it, not its name;
  anything else is refused with a plain reason. The text is read out at upload for the profile
  step that follows (#24), and a scanned PDF with no text in it is kept but flagged, since a profile
  can't be built from it. Resumes are stored in the database, so the nightly backup covers them,
  and only their owner can download one: anyone else gets "not found".
- An AI spend ledger with a monthly cap per user (#22). Every Claude call runs on Sean's one key,
  so each call is recorded against the user it was for, and a call is refused *before* it is made
  once that user has spent this month's budget (200¢ by default) or everyone together has spent
  the key's (1000¢). The page then says which, in plain words, and nothing is charged. With no key
  set, AI features say they're off instead of failing. The admin panel shows each account's total
  for the month, never what it was spent on. Nothing calls Claude yet; the profile builder (#24) is
  the first.

### Changed
- The app now runs on Python 3.15 (3.15.2), as Budget Buddy does. CI, the linter's target and the
  docs moved with it.
- Job Hunt's database backups are encrypted once they leave the droplet (#21). From this release
  they hold people's resumes, so the nightly copy kept off the server is encrypted before it touches
  disk there, and the key that opens it lives only in Sean's password manager. A restore from one
  was rehearsed end to end before this release.

## [0.1.0] - 2026-11-19

The skeleton milestone: an empty app you can log in to, deployed at `apply.seandesmet.com`.

### Added
- The skeleton, copied from Orbit Ops (#1): a FastAPI app with Postgres in its own unpublished
  container, Alembic with a least-privilege `apply_app` role, a strict CSP, an error page that never
  shows a traceback, `/healthz` with the version and a database check, `./test.sh`, and CI (lint,
  tests with a migration round-trip, an image build with an ownership check and a vulnerability scan).
- Logins (#2): email and password with argon2, sessions that end when the password is reset,
  CSRF on every POST, and a login rate limit counted in Postgres, so it holds across restarts and
  processes. `scripts/create_user.py` creates the first admin.
- Invites (#3): the admin makes a one-time set-password link that expires after 7 days, and sends
  it themselves; there's no public sign-up. The same kind of link resets a forgotten password, so
  the admin never picks or sees one. The admin can disable an account, which ends its sessions.
  The set-password page and every page's footer say what the admin can see.
- The release pipeline (#4): publishing a GitHub Release builds the image to ghcr, boots it locked
  down against a scratch database, scans it, then waits for approval before deploying, with a
  verified database dump first. `rollback.yml` puts an older version back. PRs must update the
  changelog when they change `app/`, and must claim every acceptance-criteria scenario.
- The droplet tenant as code (#5): `infra/` holds Job Hunt's layer of the shared droplet at
  `apply.seandesmet.com` (its directory, Nginx site, own deploy key and a nightly backup timer),
  on droplet-infra's host role pinned by SHA, with an LXD test. `RUNBOOK.md` covers first-time
  setup, the database role, deploys, backups and restore.

[Unreleased]: https://github.com/CaddisMaster/job-hunt/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/CaddisMaster/job-hunt/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/CaddisMaster/job-hunt/releases/tag/v0.1.0
