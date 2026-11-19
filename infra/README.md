# infra/: Job Hunt's layer of the shared droplet

The host (the `deploy` user, sshd, ufw, Docker, Nginx base, Certbot) is
[`droplet-infra`](https://github.com/CaddisMaster/droplet-infra)'s `droplet_host` role, pinned by
commit SHA in `host-role.pin`. This directory adds only Job Hunt's layer, ported from Orbit Ops's
`infra/`:

| Here | What |
|---|---|
| `ansible/roles/job_hunt_app/` | `/opt/apply` and `backups/`, an `.env` check, Job Hunt's deploy key, the Nginx site for `apply.seandesmet.com`, the nightly backup timer |
| `systemd/` | `apply-backup.service` and `.timer`, the one copy of the units |
| `test.sh` | the role in a throwaway LXD container on the `jupiter` VM |

What the role **never** does, because the box is shared and `release.yml` owns the app: write
`.env` or `docker-compose.yml`, request a certificate, or touch another app's site. It refuses to
run until `.env` (mode 600), the certificate and Job Hunt's deploy key are in place (RUNBOOK §2, §4).

## Testing in LXD

```bash
infra/test.sh           # launch → host layer → refusals → run → run again (changed=0) → check mode → delete
KEEP=1 infra/test.sh    # leave the container up
```

⚠️ **Warn Sean before a run.** The VM is on his Mac, and a run is about 5 minutes of load. It
creates and deletes only containers named `apply-*`.

## Running against production

Check mode first, always:

```bash
cp ansible/inventory.example.yml ansible/inventory.yml   # gitignored; fill in the address
cd ansible && ansible-playbook app.yml --check --diff
```
