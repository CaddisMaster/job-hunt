# Applying from Claude Code (on the VM)

How Job Hunt applies (plan §10 decisions 9–11): Sean pastes job links into the web app. In a Claude
Code session on the VM, `/profile` reads his resume into his profile, and `/apply` tailors his
resume to a job and fills in the application in a browser **Sean watches and uses** through his
Mac's Screen Sharing. **Sean clicks Submit himself.** The web app checks the tailored resume, makes
the PDF and keeps the record. Claude Code reaches it through the bridge (a personal access token,
`/api/`), always outbound; the web app never contacts the VM.

```
 VM (jupiter)                                            Sean's Mac
 Claude Code in the job-hunt folder
   ├─ jobhunt MCP (tools/jobhunt_mcp.py) ──HTTPS──▶ apply.seandesmet.com
   └─ playwright MCP → Chromium on a virtual screen (Xvfb :99)
                        └─ x11vnc on localhost:5901 ── SSH tunnel ──▶ Screen Sharing window:
                                                                     log in, CAPTCHAs, Submit
```

Everything lives on the VM: Claude Code, the skills, the token and the browser with its sign-ins.
The Mac is only the window.

## One-time setup

1. **Install the pieces Chromium and VNC need** (Sean, sudo):
   ```bash
   sudo apt-get install -y x11vnc libgbm1 libgl1-mesa-dri libglx-mesa0 mesa-libgallium
   sudo install -m 644 tools/apparmor/jobhunt-chromium /etc/apparmor.d/ \
     && sudo apparmor_parser -r /etc/apparmor.d/jobhunt-chromium
   ```
   - **The AppArmor profile:** Ubuntu 24.04 blocks the user namespaces Chromium's sandbox needs
     (`kernel.apparmor_restrict_unprivileged_userns = 1`). Without it, Chromium stops with "No
     usable sandbox!". The profile allows them for Playwright's Chromium only, the way Ubuntu's
     own `chrome` profile does for Google Chrome. Never use `--no-sandbox` instead: this browser
     opens employers' pages.
   - **Chromium itself** is Playwright's ARM build (Chrome doesn't exist for ARM Linux), already
     in `~/.cache/ms-playwright/chromium-1252/` (2026-11-20). If it's missing:
     `npx -y playwright@1.65.0-alpha-2026-10-30 install chromium`. That's the Playwright release
     `@playwright/mcp@0.0.84` uses.
2. **A VNC password, then the screen as a service** (Sean; the first command prompts, and
   Screen Sharing asks for the password, so tick "Remember this password in my keychain" there):
   ```bash
   mkdir -p ~/.config/jobhunt && x11vnc -storepasswd ~/.config/jobhunt/vncpasswd && chmod 600 ~/.config/jobhunt/vncpasswd
   tools/apply-services.sh install                          # production
   tools/apply-services.sh install http://localhost:5004    # or the dev server
   ```
   That sets up three user services, enables them and starts them:
   - `tools/systemd/jobhunt-xvfb.service`: the virtual screen, `:99`.
   - `jobhunt-vnc.service`: VNC on the VM's localhost:5901 only.
   - `jobhunt-runner.service`: generated from `jobhunt-runner.service.in` with this folder and the
     URL. It takes the runs you queue in the web app (#88).

   Lingering is on, so they run from boot without a login. `tools/apply-services.sh
   status|restart|logs|uninstall` does the rest.
3. **A Job Hunt token** (Sean, in his own terminal, since Claude Code never reads it):
   - In Job Hunt: Profile → *Access tokens for Claude Code* → **Make a token**, then copy it.
   - Save it to the file the MCP server reads (paste the token at the silent prompt):
     ```bash
     mkdir -p ~/.config/jobhunt && (umask 077 && read -rsp 'Token: ' t && printf '%s' "$t" > ~/.config/jobhunt/token) && echo
     ```
   `.claude/settings.json` denies Claude Code reading `~/.config/jobhunt/`.
4. **The two MCP servers**, from the repository folder on the VM:
   ```bash
   cd ~/Developer/personal-projects/job-hunt
   claude mcp add jobhunt -- python3 "$PWD/tools/jobhunt_mcp.py"   # against dev: add -e JOBHUNT_URL=http://localhost:5004
   claude mcp add playwright -e DISPLAY=:99 -- npx -y @playwright/mcp@0.0.84 \
     --browser chrome --executable-path "$HOME/.cache/ms-playwright/chromium-1252/chrome-linux-arm64/chrome" \
     --user-data-dir "$HOME/.local/share/jobhunt/browser" \
     --output-dir "$HOME/.local/share/jobhunt/screenshots" --viewport-size 1400x850
   ```
   - `@playwright/mcp` is pinned to the newest release that was at least two weeks old on
     2026-11-20. Bump it on purpose, never with `@latest`.
   - `--user-data-dir` keeps the browser's sign-ins between sessions. Without it, every session
     starts signed out.
   - `--browser chrome` with `--executable-path` runs Playwright's Chromium, because 0.0.84 has
     no `chromium` choice.
5. **Check.** Start `claude` in the folder and run `/mcp`. Both servers should be connected. Then
   ask "list my saved jobs in Job Hunt": an empty list or your jobs means the token works.

## From the web app (the usual way)

1. **Keep the window handy:** Screen Sharing on the Mac (`vnc://localhost:5901`, through the
   tunnel). You only need it when a run asks for the browser.
2. **On a saved job's page, press Apply** (or **Build my profile** on the Profile page). The **Run**
   panel appears and updates by itself:
   - **Progress:** what it's doing.
   - **Needs you:** a question, with buttons for its options or a box to type in. Approving the
     tailored resume works the same way.
   - **Needs you in the browser:** sign in or answer a CAPTCHA in Screen Sharing, then press
     **Done**.
   - **Ready to submit:** the filled form's screenshot. **Nothing is sent until you press Submit
     application.** Don't submit stops it there.
   - **Stop**, at any time.
3. The run records the application and what it learned about the site, and the panel says how it
   ended.

Behind it, the runner on the VM claims the run and starts Claude Code headless, with only the
`jobhunt` and `playwright` tools, and the skill's web mode as its instructions. A question left
unanswered for 30 minutes stops the run. `tools/apply-services.sh logs` shows what the runner did.

## From a Claude Code session (still works)

1. **On the Mac, the window:** open the tunnel (`ssh -N -L 5901:localhost:5901 sean@jupiter`,
   left running), then `open vnc://localhost:5901`. To never type the tunnel again, add
   `LocalForward 5901 localhost:5901` under `Host jupiter` in the Mac's `~/.ssh/config`: it's then
   open whenever VS Code is connected to the VM. The screen is blank until the browser opens.
2. **On the VM:** `claude` in the folder, then **`/apply next`** (or `/apply 12`):
   - It proposes a tailored resume and says what it led with. Steer it, then approve it.
   - It opens the application in the browser, which you see in Screen Sharing, and fills it.
     Anything your answer bank doesn't cover, it asks you, and it asks before saving the answer.
   - It stops on the last page. **You** check it and click Submit in the Screen Sharing window,
     then tell it. It records the application with a screenshot and notes what it learned about
     the site.
   - Then `/apply next` again.

**Once, before the first `/apply`:** upload your resume on the Profile page, run **`/profile`**, then
review and confirm the profile at `/profile/review`. Fill in your answer bank while you're there:
every question it answers is one `/apply` won't ask.

## Where things are

| | |
|---|---|
| The skills | `.claude/skills/apply/SKILL.md`, `.claude/skills/profile/SKILL.md` |
| The services | `tools/systemd/` (screen, VNC, runner), managed by `tools/apply-services.sh` |
| The runner | `tools/jobhunt_runner.py`; runs and their prompts: `app/runs.py`, the panel `app/templates/_run_panel.html` |
| The MCP server | `tools/jobhunt_mcp.py`: stdlib only; the token from `~/.config/jobhunt/token` (mode 600) |
| The browser's profile and screenshots | `~/.local/share/jobhunt/` |
| The API | `app/routers/api.py`, bearer token only; tokens in `app/tokens.py` |
| The checks | `app/tailor.py` (the tailored resume), `app/profile.py` (`ground`) |

**Revoking:** Profile → the token → **Revoke**, and delete `~/.config/jobhunt/token`.
