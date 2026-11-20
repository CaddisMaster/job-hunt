# Applying from Claude Code (on Sean's Mac)

How Job Hunt applies (plan §10 decisions 9–11): Sean pastes job links into the web app. In a Claude
Code session on his Mac, `/profile` reads his resume into his profile, and `/apply` tailors his
resume to a job and fills in the application in a **visible** browser. **Sean clicks Submit
himself.** The web app checks the tailored resume, makes the PDF and keeps the record. Claude Code
reaches it through the bridge (a personal access token, `/api/`).

```
 Web app (apply.seandesmet.com)              Sean's Mac
 Saved jobs · profile · answer bank          Claude Code in the job-hunt folder
 Tailored resume: check, PDF, approve  ◀──▶    ├─ jobhunt  (tools/jobhunt_mcp.py → the bridge)
 Applications · site notes                     └─ playwright (a visible browser: you log in,
                                                                you answer CAPTCHAs, you click Submit)
```

**Not on the VM.** The browser opens on the machine running Claude Code, so it has to be the one in
front of Sean.

## One-time setup

You need Claude Code, Python 3 (macOS has it) and Node.js (for Playwright MCP), plus a clone of this
repository.

1. **Make a token.** In Job Hunt: Profile → *Access tokens for Claude Code* → name it ("Claude Code
   on my Mac") → **Make a token**. It's shown once; copy it.
2. **Put it in the Keychain**, never in a file:
   ```bash
   security add-generic-password -s jobhunt-token -a "$USER" -w
   ```
   This prompts for the password: paste the token. To replace it later, add `-U`.
3. **Add the two MCP servers**, from the repository folder:
   ```bash
   cd ~/path/to/job-hunt
   claude mcp add jobhunt -- python3 "$PWD/tools/jobhunt_mcp.py"
   claude mcp add playwright -- npx -y @playwright/mcp@0.0.84
   ```
   `@playwright/mcp` is pinned to the newest release that was at least two weeks old on
   2026-11-20. Bump it on purpose, never with `@latest`. By default it opens a visible browser with
   its own persistent profile, so a site you sign in to stays signed in on this Mac and nowhere
   else.
4. **Check.** Start `claude` in the folder and run `/mcp`. Both `jobhunt` and `playwright` should
   be connected. Then ask "list my saved jobs in Job Hunt": an empty list or your jobs means the
   token works.

Against a dev server instead of production: add `-e JOBHUNT_URL=http://<host>:5004` to the
`jobhunt` line.

## Using it

1. **Once:** upload your resume on the Profile page, run **`/profile`** in Claude Code, then review
   and confirm the profile at `/profile/review`. Fill in your answer bank (Profile → Answer bank)
   while you're there: every question it answers is one `/apply` won't have to ask.
2. **Any time, from any device:** paste job links into **Saved jobs**.
3. **When you sit down to apply:** `claude` in the folder, then **`/apply next`** (or `/apply 12`).
   - It proposes a tailored resume and tells you what it led with. Steer it if you like, then
     approve it.
   - It opens the application in the browser and fills it. It asks you anything your answer bank
     doesn't cover, and asks before saving the answer.
   - It stops on the last page. **You** check it and click Submit, then tell it. It records the
     application with a screenshot and notes what it learned about the site.
   - Then `/apply next` again.

## Where things are

| | |
|---|---|
| The skills | `.claude/skills/apply/SKILL.md`, `.claude/skills/profile/SKILL.md` |
| The MCP server | `tools/jobhunt_mcp.py`: stdlib only; the token from the Keychain (`jobhunt-token`) |
| The API | `app/routers/api.py`, bearer token only; tokens in `app/tokens.py` |
| The checks | `app/tailor.py` (the tailored resume), `app/profile.py` (`ground`) |

**Revoking:** Profile → the token → **Revoke**. The MCP server's next call gets a 401 and says so.
