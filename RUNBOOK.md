# Operations Runbook

How Job Hunt runs on the DigitalOcean droplet it **shares with Budget Buddy and Orbit Ops**. The
host itself is code in [`droplet-infra`](https://github.com/CaddisMaster/droplet-infra); this repo's
`infra/` is only Job Hunt's layer on top (`infra/README.md`). Orbit Ops's `RUNBOOK.md` is the
longer original of most of this.

> ⚠️ Host names, the droplet IP and the deploy user's details are not in this repo. They live in the
> gitignored `CLAUDE.local.md` and the vault.

## 1. What runs where

| App | URL | Directory | Host port → container | Database |
|---|---|---|---|---|
| Budget Buddy | budget.seandesmet.com | `/opt/budget-buddy` | `127.0.0.1:5001` → 5000 | own `db` |
| Orbit Ops | learn.seandesmet.com | `/opt/orbit-ops` | `127.0.0.1:5002` → 8000 | own `db`, not published |
| **Job Hunt** | **apply.seandesmet.com** | **`/opt/apply`** | **`127.0.0.1:5004` → 8000** | **own `db`, not published** |

Every published port is bound to `127.0.0.1`: Docker's port publishing bypasses ufw.

**Memory.** The droplet has 2 GB (`s-1vcpu-2gb`). Job Hunt caps `web` and `db` at 256 MB each.
Check headroom before the first deploy:

```bash
free -m && docker stats --no-stream && swapon --show
```

⚠️ **Resize to 4 GB before 0.6** (plan §10): the applier runs headless Chromium. The resize is a
change in `droplet-infra/infra/terraform/main.tf`, made and checked before the applier ships.

## 2. First-time setup (once)

`deploy` has no sudo, so anything below with `sudo` runs as root; the rest runs as `deploy`
(`.env` must end up `deploy:deploy`). Steps 2, 5 and 7 are described as code in `infra/ansible/` and can
be applied or checked with it (`infra/README.md`); the rest stays by hand, and the role refuses to
run without them.

```bash
# 1. DNS: an A record for `apply` → the droplet's IP, by hand at Squarespace.
dig +short apply.seandesmet.com          # must print the droplet IP before step 4

# 2. Deploy directory (as root)
sudo install -d -o deploy -g deploy -m 750 /opt/apply
sudo install -d -o deploy -g deploy -m 700 /opt/apply/backups

# 3. .env — from .env.example, filled in (§3). Made as root, it must end up deploy's, mode 600.
cd /opt/apply && (umask 077 && $EDITOR .env) && sudo chown deploy:deploy .env && sudo chmod 600 .env

# 4. The certificate (as root). certonly: Certbot never edits the site file, so the
#    role's copy (infra/ansible/roles/job_hunt_app/files/) stays byte-identical.
sudo certbot certonly --nginx -d apply.seandesmet.com
sudo certbot certificates                # one lineage covering apply.*

# 5. Nginx site, deploy key and backup timer: run the app role (infra/README.md), or by hand
#    copy files/apply.seandesmet.com into sites-available, link it, nginx -t, reload.

# 6. The package is private (the repo is), so the droplet must log in to pull. A
#    fine-grained or classic PAT with read:packages only:
echo <PAT> | docker login ghcr.io -u CaddisMaster --password-stdin

# 7. Cut the first GitHub Release (§5). The pipeline ships docker-compose.yml, creates the
#    db, migrates and starts the app. Then make the first admin:
docker compose exec web python -m scripts.create_user <email> --admin
```

Security headers (CSP, HSTS, X-Frame-Options…) are set by the app, not Nginx, and tested in
`tests/test_security_headers.py`. Don't duplicate them in the site file.

## 3. Environment and the database role

`.env` keys are documented in `.env.example`. Production values that differ:

| Key | Production |
|---|---|
| `TAG` | written by the deploy — never edit by hand except in break-glass |
| `COOKIE_SECURE` | `1` |
| `DB_APP_USER` / `DB_APP_PASSWORD` | `apply_app` / a generated password (below) |
| `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |

**The least-privilege role.** Migration `0001` creates `apply_app` with DML on every table but no
DDL and **no password**. After the first deploy has run the migrations:

```bash
cd /opt/apply && set -a && . ./.env && set +a
PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -c "ALTER ROLE apply_app PASSWORD '$PW'"
# put DB_APP_USER=apply_app and DB_APP_PASSWORD=$PW in .env, then:
docker compose up -d --no-deps web
curl -s http://127.0.0.1:5004/healthz      # {"status":"ok",...}
```

**The recovery copy.** Keep a copy of `.env` in Bitwarden as `job-hunt-env-prod`, every key a
custom field, the same way as Orbit Ops's (its RUNBOOK §3, "The recovery copy in Bitwarden").
Edit the droplet's `.env` first, then the item.

## 4. GitHub configuration (once)

- **Deploy key.** Job Hunt gets its own, not another tenant's:
  ```bash
  ssh-keygen -t ed25519 -N '' -C 'github-actions job-hunt deploy' -f job-hunt-deploy
  ```
  The `.pub` line goes in `infra/ansible/roles/job_hunt_app/defaults/main.yml`
  (`job_hunt_deploy_key`); the private half goes in the `DROPLET_SSH_KEY` secret below. Then
  delete both local files.
- **Environment `production`** with required reviewer = Sean.
  - Secrets: `DROPLET_SSH_KEY`, `DROPLET_HOST`, and `DROPLET_KNOWN_HOSTS` built from the
    droplet's own `/etc/ssh/ssh_host_*_key.pub` (each line prefixed with the host), **not**
    `ssh-keyscan`, which trusts whatever answers on the network. Check it against a
    known_hosts you already trust.
  - Var: `DROPLET_USER` (`deploy`).
- **Branch protection on `main`**: require checks `Lint`, `Tests`, `Image builds`,
  `App changes update the changelog`, `Issue criteria are claimed by tests`; squash-merge only.
- **Settings → Code security**: secret scanning + push protection on.

## 5. Deploying and rolling back

- **Deploy:** publish a GitHub Release `vX.Y.Z` (`VERSIONING.md`). `release.yml` builds and pushes
  the image, migrates and boots it locked down against a scratch database, scans it, then waits
  for approval on `production`. After approval it ships `docker-compose.yml` and `backup_db.sh`,
  takes a verified dump, migrates from the new image while the old one serves, swaps `web`, and
  checks `https://apply.seandesmet.com/healthz` reports the version.
- **Roll back:** run `rollback.yml` with an older version. It never runs migrations: they're
  expand-only within a release, so the old image runs on the new schema. To undo a schema too,
  restore the pre-deploy dump (§6).
- **Changing the `db` service** recreates the database container: `scripts/install_compose.sh`
  refuses such a release. Follow Orbit Ops's RUNBOOK §5 for the scheduled operation.

## 6. Backups and restore

- **Pre-deploy:** every deploy takes `backups/pre-deploy-*.sql.gz` first (the last 14 kept).
- **Nightly:** `apply-backup.timer` runs `/opt/apply/backup_db.sh nightly 14` at 03:45 New York
  time, a quarter hour after Orbit Ops's.
- **Off the droplet:** `backup-export` (droplet-infra) is the one way dumps leave the box; the
  `jupiter` VM pulls them with a key bound to it. It needs a `job-hunt` command, the same shape as
  `orbit-ops` (a cross-repo change in droplet-infra).

⚠️ **Before inviting anyone else** (from 0.2 the dumps hold other people's resumes): confirm where
the VM keeps the pulled dumps, that they're encrypted at rest there, and how long they're kept.
Record the answer in #5.

**Restore** (the database only; stop `web` first):

```bash
cd /opt/apply && docker compose stop web
set -a && . ./.env && set +a
gunzip -c backups/<file>.sql.gz | docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1
docker compose start web
```

(The dump is a plain `pg_dump`; restore into an empty database, or drop and recreate it first.)

## 7. Health and troubleshooting

```bash
curl -s https://apply.seandesmet.com/healthz
cd /opt/apply && docker compose ps && docker compose logs --tail 100 web
systemctl list-timers apply-backup.timer && systemctl --failed
```
