# `.claude/` — the harness half

`CLAUDE.md` says what the agreement is; this directory makes some of it execute. If anything here
disagrees with `CLAUDE.md`, `CONTRIBUTING.md` or the workflows, this directory is what needs
fixing. Ported from Orbit Ops (#13).

```
.claude/
  commands/wrap.md          # /wrap — the end-of-session sequence
  skills/apply/SKILL.md     # /apply — tailor the resume, fill the application, Sean clicks Submit (docs/apply.md)
  skills/profile/SKILL.md   # /profile — read the resume into the profile, for Sean to confirm
  hooks/changelog-guard.sh  # Stop hook: the CHANGELOG rule, locally (mirrors changelog.yml)
  settings.json             # permission allowlist + hook wiring
```

## Mind the line

| Committed `settings.json` | Gitignored `settings.local.json` |
|---|---|
| Repo-relative commands: `./test.sh`, compose, read-only `git`/`gh` | Absolute `$HOME` paths |
| Anything true for any clone | `ssh`, host names, the deploy user, backup paths |

The deny rules — reading `.env`, force-pushing — stay in the committed file so every clone gets
them.

## The Stop hook

If the branch changed `app/` and not `CHANGELOG.md`, it says so once, then stays quiet. It fails
open on anything it cannot understand. Silence it locally with `SKIP_CHANGELOG_GUARD=1`; on the PR
the escape hatch is the `skip-changelog` label. `tests/test_changelog_guard.py` drives it in a
scratch repository.

Smoke test it by hand on a branch that changes `app/` (exit 2 and the message means it works):

```bash
echo '{"stop_hook_active": false}' | .claude/hooks/changelog-guard.sh; echo "exit $?"
```

## Not ported (yet)

Orbit Ops's `verify` skill comes back when there are flows worth driving (0.2+). Its
`beginner-audit` skill and its agents (gotcha-auditor, release-prep, content-reviewer) are left
out until Job Hunt needs them.
