---
description: Run the end-of-session wrap-up — PR, verify at localhost:5004, notes in one pass, merge.
---

Run the session wrap-up for Job Hunt.

`CLAUDE.md` §"Versioning, git, release" is the source of truth for *policy*. This command carries
the **sequence** and the **traps**. If the two disagree, `CLAUDE.md` wins and this file is what
needs fixing.

⚠️ Host names, the deploy user and vault paths never go in this file — they live in the
gitignored `CLAUDE.local.md`.

Work through the steps in order, reporting after each. Stop and ask rather than skipping — the
ordering is the point.

## 1. Open a PR for the locally-tested unit(s)

Do not work directly on `main`.

- Confirm `./test.sh` has actually been run in full this session. If not, run it now. CI does not
  click through the app, and Jinja's silent-empty-string failure mode means a template typo
  renders as blank rather than raising — content-asserting tests are the only net.
- If anything under `app/` changed, add a `CHANGELOG.md` entry under `## [Unreleased]`, written
  for someone reading it in a year: what changed and why it matters, not what the diff says.
  (The `Stop` hook in `.claude/hooks/` will catch you if you forget, but it fires at the end —
  writing it here is cheaper.)
- Open the PR with `Closes #<issue>`, one line per issue closed — or `Part of #<issue>` when it
  doesn't finish one. Every `Scenario:` of a closed issue needs a `criterion` test or a
  `Verified by hand: #N "<Scenario title>" — <evidence>` line in the PR body, or the criteria
  check fails.
- **Batch by coherence, never by calendar.** A shared file surface, test surface, or one
  user-facing story. A migration **always stands alone** — one Alembic revision per PR, nothing
  else in it.

## 2. Rebuild and verify locally

```bash
docker compose up -d --build web
```

Confirm it is actually serving at <http://localhost:5004> — not merely that the container
started (`curl -s http://localhost:5004/healthz`). If the change touched a flow rather than a
template, drive it: log in, click through, and say what you saw.

## 3. Update the notes — in ONE pass, at the very end

This is a standing rule and it is deliberate: note-writing happens once, at the end, not
incrementally through the session.

- `docs/status.md`: rewrite *Pick up here* so it is true, and add this session's entry to the top
  of *Sessions* — what merged, what was learned that the code doesn't say. Keep the three most
  recent sessions; move older ones, unchanged, to the top of `docs/status-archive.md` (create it
  the first time).
- `CLAUDE.md` *Current status*: it points at `docs/status.md`. Change it only if that stopped
  being enough, and keep `CLAUDE.md` small.
- The Claude project memory, in the same pass.
- If `CLAUDE.local.md` exists, read it: anything it says lives outside the repo is updated here
  too.

These notes ride in the session's last PR, or a PR of their own.

## 4. Get it merged

Push, wait for CI green, squash-merge — then **check the run on `main`**.

A green PR does not guarantee a green `main`: the squash commit is a new tree, and a PR that
merged after another can break in combination. So after every squash-merge run
`gh run list --branch main --workflow ci.yml --limit 1` and read it before starting the next
thing. ⚠️ **Do not re-run a red one before reading it** — a race goes green on re-run and hides.

⚠️ **Do not push to a branch whose PR may already have been merged.** In Budget Buddy, PR #113
was merged while a follow-up commit was being pushed to its branch; the commit landed on the
branch and never reached `main`. The symptom is the PR head frozen at an older SHA while the
branch ref moves on.

### Stacked branches

Force-push is denied here (`.claude/settings.json`), so the usual "rebase onto `main` after a
squash-merge" isn't available. What worked for #7–#10:

1. On the next branch, `git merge origin/main`.
2. Each add/add conflict is the same file at an earlier stage — keep the branch's side
   (`git checkout --ours <file>`), then `git add` and commit.
3. Check the merge changed nothing: `git diff <branch-tip-before-the-merge> HEAD` must be empty.
4. Push, retarget the PR to `main` (`gh pr edit <N> --base main`), and its diff now shows only
   its own work.

## What is NOT part of the wrap-up

**Releasing.** Publishing a GitHub Release, the `production` approval gate and the deploy are a
separate event with their own sequence — see `VERSIONING.md` (and `RUNBOOK.md` once #5 lands). A session
that merged three PRs has not shipped anything, and `/wrap` should not imply otherwise.

Finish by telling me in three or four lines: what merged, what is still open, and what the next
session should pick up.
