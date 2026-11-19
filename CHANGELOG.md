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
