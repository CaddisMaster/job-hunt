# Changelog

All notable changes to Job Hunt. The format follows [Keep a Changelog](https://keepachangelog.com);
versions follow `VERSIONING.md`. Write entries for someone reading them in a year: what changed
and why it matters, not what the diff says.

## [Unreleased]

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
